from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
from typing import Any, Callable

from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command, Send

from core.database import db
from core.context.delegation import build_delegation_context, latest_delegation_context
from core.delegation_broker import task_brief_requires_child_delegation
from core.delegation_result_contract import build_delegation_result_contract
from core.observability_db import redact_observability_text
from core.response_normalizer import extract_text_and_reasoning
from core.runtime_continuation import (
    RuntimeContinuationContractError,
    normalize_runtime_continuation_request,
)
from core.engineering_capsule import (
    derive_grandchild_engineering_task,
    effective_engineering_capsule,
    engineering_capsule_mode,
)
from core.workspace_capability import build_workspace_binding
from core.runtime_episodes import (
    RuntimeEpisodeDurabilityError,
    append_handoff_ref,
    build_handoff_ref,
    build_runtime_episode,
    emit_runtime_episode_event,
    enqueue_runtime_episode,
    heartbeat_runtime_episode,
    persist_handoff_ref,
    persist_runtime_episode,
    transition_runtime_episode,
    upsert_runtime_episode,
)
from erc.runtime_context import bind_runtime_context, build_runtime_callback_config
from .route_context import merge_route_context


RUNTIME_EPISODE_WAIT_NODE = "runtime_episode"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ParallelBranchExecutionError(RuntimeError):
    """Branch failure carrying only the compact execution truth needed for recovery."""

    def __init__(self, message: str, *, compact_trace: str = "", tools_used: list[str] | None = None) -> None:
        super().__init__(message)
        self.compact_trace = str(compact_trace or "")[:2400]
        self.tools_used = list(dict.fromkeys(str(item) for item in list(tools_used or []) if str(item).strip()))[:12]


def _publish_parallel_progress(callback: Callable[[dict[str, Any]], Any] | None, **payload: Any) -> None:
    if callback is None:
        return
    try:
        callback({key: value for key, value in payload.items() if value not in (None, "", [], {})})
    except Exception:
        # Progress is observability only and must never change branch semantics.
        return


def _parallel_branch_error(
    message: str,
    *,
    state: dict[str, Any],
    initial_message_count: int,
) -> ParallelBranchExecutionError:
    messages = list(state.get("messages") or [])[initial_message_count:]
    return ParallelBranchExecutionError(
        message,
        compact_trace=_compact_transcript(messages, limit=2400),
        tools_used=_extract_tool_names(messages),
    )


def _render_delegation_handoff_message(
    *,
    invocation_id: str,
    expected: int,
    contracts: list[dict[str, Any]],
) -> HumanMessage:
    failures = [item for item in contracts if str(item.get("status") or "").strip().lower() != "ok"]
    payload = json.dumps(contracts, ensure_ascii=False, separators=(",", ":"))
    return HumanMessage(
        content=(
            "[V8OS 子代理结构化回流]\n"
            f"本次已回收 {len(contracts)}/{expected or len(contracts)} 个结果，失败 {len(failures)} 个。\n"
            "下面是可直接验收的完整协作合同；它已经剔除 runtime 调度噪声，但保留任务、lineage、结果、自检、产物和验收动作。\n"
            f"<delegation_handoffs>{payload}</delegation_handoffs>\n"
            "精确子 Agent 输出只读取 resultText；summary/compactTranscript 仅供展示，不得用包装文案替代结果。"
            "你必须逐项明确 accept、retry 或 ignore；只依据上述合同验收，不要把内部 ID 当作用户说明。"
            "在面向用户的结论中用独立一行记录父级决定：`验收决定：ACCEPT`、`验收决定：RETRY` "
            "或 `验收决定：IGNORE`。"
        ),
        id=str(uuid.uuid4()),
        additional_kwargs={
            "v8_governance_type": "delegation_handoff",
            "v8_delegation_invocation_id": invocation_id,
            "v8_delegation_handoffs": contracts,
        },
    )


def _runtime_context_from_parallel_state(state: dict[str, Any], *, branch: dict[str, Any] | None = None) -> dict[str, Any]:
    state = dict(state or {})
    route_context = dict(state.get("current_route_context") or {})
    branch = dict(branch or state.get("parallel_branch") or {})
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    task_context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    task_capsule = effective_engineering_capsule(task_brief)
    capsule_mode = engineering_capsule_mode(task_brief)
    execution_contract = (
        task_context.get("engineeringExecutionContract")
        if isinstance(task_context.get("engineeringExecutionContract"), dict)
        else {}
    )
    allowed_write_paths: list[str] = []
    if capsule_mode == "write":
        for source in (task_brief, task_capsule, execution_contract):
            for key in ("writeSet", "write_set", "allowedWorkset", "allowed_workset"):
                raw_values = source.get(key)
                values = [raw_values] if isinstance(raw_values, str) else list(raw_values or [])
                for raw_value in values:
                    value = raw_value.get("path") if isinstance(raw_value, dict) else raw_value
                    normalized = str(value or "").strip()
                    if normalized and normalized not in allowed_write_paths:
                        allowed_write_paths.append(normalized)
    context = {
        "runtime_kind": "subagent",
        "actor_role": "grandchild" if int(branch.get("delegationDepth") or 1) >= 2 else "direct_subagent",
        "trigger_source": "delegation_broker",
        "session_id": state.get("session_id") or state.get("sessionId") or route_context.get("session_id") or route_context.get("sessionId"),
        "run_id": state.get("run_id") or state.get("runId") or route_context.get("run_id") or route_context.get("runId"),
        "workspace_path": state.get("workspace_path") or state.get("workspacePath") or route_context.get("workspace_path") or route_context.get("workspacePath"),
        "workspace_id": state.get("workspace_id") or state.get("workspaceId") or route_context.get("workspace_id") or route_context.get("workspaceId"),
        "project_id": state.get("project_id") or state.get("projectId") or route_context.get("project_id") or route_context.get("projectId"),
        "resolved_scope": state.get("resolved_scope") or state.get("resolvedScope") or route_context.get("resolved_scope") or route_context.get("resolvedScope"),
        "goal": branch.get("reason") or branch.get("taskGoal") or branch.get("taskBrief"),
        "delegation_id": branch.get("delegationId"),
        "delegation_depth": int(branch.get("delegationDepth") or 1),
        "parent_delegation_id": branch.get("parentDelegationId"),
        "root_episode_id": (
            branch.get("rootEpisodeId")
            or route_context.get("rootEpisodeId")
            or route_context.get("root_episode_id")
        ),
        "subagent_id": branch.get("agentId"),
        "agent_id": branch.get("agentId"),
        "safety_approval_mode": (
            state.get("safety_approval_mode")
            or state.get("safetyApprovalMode")
            or route_context.get("safety_approval_mode")
            or route_context.get("safetyApprovalMode")
        ),
        "engineering_capsule_mode": capsule_mode,
        "engineering_capsule_id": task_capsule.get("capsuleId"),
        "engineering_task_capsule": task_capsule or None,
        "allowed_write_paths": allowed_write_paths or None,
        "original_workspace_path": (
            state.get("original_workspace_path")
            or state.get("originalWorkspacePath")
            or route_context.get("original_workspace_path")
            or route_context.get("originalWorkspacePath")
        ),
        "repository_root": state.get("repository_root") or route_context.get("repository_root"),
        "worktree_root": state.get("worktree_root") or route_context.get("worktree_root"),
        "worktree_id": state.get("worktree_id") or route_context.get("worktree_id"),
        "sandbox_lease_id": state.get("sandbox_lease_id") or route_context.get("sandbox_lease_id"),
        "sandbox_policy": state.get("sandbox_policy") or route_context.get("sandbox_policy"),
        "sandbox_policy_digest": (
            state.get("sandbox_policy_digest") or route_context.get("sandbox_policy_digest")
        ),
        "sandbox_policy_file": state.get("sandbox_policy_file") or route_context.get("sandbox_policy_file"),
        "sandbox_capabilities": (
            state.get("sandbox_capabilities") or route_context.get("sandbox_capabilities")
        ),
        "managed_engineering_execution": (
            state.get("managed_engineering_execution")
            or state.get("managedEngineeringExecution")
            or route_context.get("managed_engineering_execution")
            or route_context.get("managedEngineeringExecution")
        ),
    }
    managed_workspace = (
        dict(branch.get("engineeringWorkspace") or {})
        if isinstance(branch.get("engineeringWorkspace"), dict)
        else {}
    )
    if managed_workspace:
        context.update(managed_workspace)
        context["runtime_kind"] = "subagent"
        context["actor_role"] = "grandchild" if int(branch.get("delegationDepth") or 1) >= 2 else "direct_subagent"
    context["workspace_binding"] = build_workspace_binding(context, runtime_kind="subagent").as_dict()
    return {key: value for key, value in context.items() if value is not None and str(value).strip()}


def _delegation_summary_allows_changeset_promotion(summary: dict[str, Any]) -> bool:
    """Return whether a delegated result may alter its parent/integration branch.

    Finalizing a managed worktree preserves the candidate for audit and recovery;
    it is not evidence that the delegated task succeeded.  Promotion therefore
    requires an explicit successful result (or an explicitly continuable degraded
    result) and never infers success from the presence of files or artifact refs.
    """

    status = str(summary.get("status") or "").strip().lower()
    if status in {"ok", "ready", "success", "completed", "done"}:
        return True
    return status == "degraded" and bool(summary.get("canContinueParent"))


def _finalize_managed_branch_workspace(branch: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    managed = branch.get("engineeringWorkspace") if isinstance(branch.get("engineeringWorkspace"), dict) else {}
    worktree_id = str(managed.get("worktree_id") or managed.get("worktreeId") or "").strip()
    if not worktree_id:
        return summary
    status = str(summary.get("status") or "").strip().lower()
    if status in {"waiting", "waiting_child", "waiting_child_delegation", "waiting_dependency"}:
        return summary
    from core.engineering_sandbox.service import get_engineering_sandbox_service

    sandbox_service = get_engineering_sandbox_service()
    try:
        change_set = sandbox_service.finalize_task_workspace(
            worktree_id=worktree_id,
            commit_message=(
                "V8OS delegated task: "
                f"{str(branch.get('taskBriefId') or branch.get('reason') or worktree_id).strip()[:120]}"
            ),
        )
    except Exception as exc:
        error_code = str(getattr(exc, "code", None) or str(exc) or exc.__class__.__name__).strip()
        error_details = dict(getattr(exc, "details", None) or {})
        violations = [
            str(value).strip()
            for value in list(error_details.get("violations") or [])
            if str(value).strip()
        ][:40]
        declared_write_set = [
            str(value).strip()
            for value in list(error_details.get("writeSet") or [])
            if str(value).strip()
        ][:40]
        worker_reported_summary = str(summary.get("summary") or summary.get("compactTranscript") or "").strip()[:1200]
        worker_reported_result = str(summary.get("resultText") or "").strip()[:1800]
        candidate_artifact_refs: list[dict[str, Any]] = []
        for ref in list(summary.get("artifactRefs") or summary.get("artifacts") or [])[:24]:
            candidate = dict(ref) if isinstance(ref, dict) else {"ref": str(ref)}
            candidate.update({"accepted": False, "state": "quarantined_unmerged"})
            candidate_artifact_refs.append(candidate)
        violation_summary = f" Undeclared paths: {', '.join(violations)}." if violations else ""
        authoritative_summary = (
            f"Managed worktree rejected: {error_code}.{violation_summary} "
            "The candidate is quarantined and is not delivery evidence."
        ).strip()
        repair_action = (
            "Repair the Engineering task contract/writeSet and route one bounded retry. "
            "Do not inspect, execute, copy, or manually reconstruct files from the preserved candidate worktree."
        )
        return {
            **summary,
            "status": "error",
            "error": f"managed_worktree_finalize_failed:{error_code}",
            "errorMessage": str(exc)[:600],
            "summary": authoritative_summary,
            "resultText": authoritative_summary,
            "localSelfCheck": authoritative_summary,
            "workerReportedSummary": worker_reported_summary,
            "workerReportedResultText": worker_reported_result,
            "artifactRefs": candidate_artifact_refs,
            "artifactRefsAccepted": False,
            "repairAction": repair_action,
            "sandboxEvidence": {
                "worktreeId": worktree_id,
                "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
                "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
                "state": "failed",
                "candidateState": "quarantined_unmerged",
                "errorCode": error_code,
                **({"violations": violations} if violations else {}),
                **({"writeSet": declared_write_set} if declared_write_set else {}),
                "repairAction": repair_action,
            },
        }
    change_set_payload = change_set.as_dict()
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    capsule = (
        task_brief.get("engineeringTaskCapsule")
        if isinstance(task_brief.get("engineeringTaskCapsule"), dict)
        else {}
    )
    write_required = not bool(task_brief.get("readOnly")) and bool(
        task_brief.get("writeRequired")
        or task_brief.get("writeSet")
        or capsule.get("writeRequired")
        or capsule.get("allowedWorkset")
    )
    promotion_allowed = _delegation_summary_allows_changeset_promotion(summary)
    if promotion_allowed and write_required and not list(change_set.changed_paths):
        sandbox_service.preserve_task_workspace_unmerged(
            worktree_id=worktree_id,
            reason="managed_worktree_no_declared_changes",
        )
        return {
            **summary,
            "status": "error",
            "error": "managed_worktree_no_declared_changes",
            "gitChangeSet": change_set_payload,
            "localSelfCheck": (
                "The task required a workspace write, but the managed worktree finalized with no changed paths. "
                "A pre-existing file reference or prose tool call is not write evidence."
            ),
            "sandboxEvidence": {
                "worktreeId": worktree_id,
                "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
                "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
                "state": "no_changes",
                "errorCode": "managed_worktree_no_declared_changes",
            },
        }
    artifact_refs = list(summary.get("artifactRefs") or [])
    artifact_refs.append(
        {
            "kind": "git_changeset",
            "ref": f"git://{change_set.repository_id}/{change_set.commit_id}",
            "commitId": change_set.commit_id,
            "changedPaths": list(change_set.changed_paths),
            "accepted": promotion_allowed,
        }
    )
    if not promotion_allowed:
        sandbox_service.preserve_task_workspace_unmerged(
            worktree_id=worktree_id,
            reason=str(summary.get("error") or f"delegation_result_{status or 'unknown'}"),
        )
        return {
            **summary,
            "gitChangeSet": change_set_payload,
            "artifactRefs": artifact_refs,
            "sandboxEvidence": {
                "worktreeId": worktree_id,
                "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
                "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
                "capabilities": managed.get("sandbox_capabilities") or managed.get("sandboxCapabilities"),
                "state": "preserved_unmerged",
                "mergeEligibility": "rejected",
                "resultStatus": status or "unknown",
            },
        }
    parent_merge: dict[str, Any] | None = None
    parent_worktree_id = str(
        managed.get("parent_worktree_id") or managed.get("parentWorktreeId") or ""
    ).strip()
    if parent_worktree_id and change_set.status in {"candidate", "no_changes"}:
        try:
            parent_merge = sandbox_service.merge_child_change_set_to_parent(
                child_worktree_id=worktree_id,
                run_id=str(managed.get("run_id") or managed.get("runId") or branch.get("invocationId") or "nested"),
            )
        except Exception as exc:
            error_code = str(getattr(exc, "code", None) or str(exc) or exc.__class__.__name__).strip()
            return {
                **summary,
                "status": "error",
                "error": f"managed_parent_merge_failed:{error_code}",
                "gitChangeSet": change_set_payload,
                "localSelfCheck": (
                    "The grandchild change set is preserved, but it could not be merged back into the parent worktree. "
                    "The parent must not report the child artifact as present."
                ),
                "sandboxEvidence": {
                    "worktreeId": worktree_id,
                    "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
                    "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
                    "state": "merge_failed",
                    "errorCode": error_code,
                },
            }
    return {
        **summary,
        "gitChangeSet": change_set_payload,
        **({"parentWorktreeMerge": parent_merge} if parent_merge else {}),
        "artifactRefs": artifact_refs,
        "sandboxEvidence": {
            "worktreeId": worktree_id,
            "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
            "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
            "capabilities": managed.get("sandbox_capabilities") or managed.get("sandboxCapabilities"),
            "state": "completed",
        },
    }


def _fail_managed_branch_workspace(branch: dict[str, Any], error_code: str) -> dict[str, Any]:
    managed = branch.get("engineeringWorkspace") if isinstance(branch.get("engineeringWorkspace"), dict) else {}
    worktree_id = str(managed.get("worktree_id") or managed.get("worktreeId") or "").strip()
    if not worktree_id:
        return {}
    normalized_error = re.sub(r"[^a-z0-9._-]+", "_", str(error_code or "branch_failed").lower()).strip("_")
    normalized_error = normalized_error[:120] or "branch_failed"
    evidence = {
        "worktreeId": worktree_id,
        "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
        "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
        "state": "failed",
        "errorCode": normalized_error,
    }
    try:
        from core.engineering_sandbox.service import get_engineering_sandbox_service

        get_engineering_sandbox_service().mark_task_workspace_failed(
            worktree_id=worktree_id,
            error_code=normalized_error,
        )
    except Exception as exc:
        evidence["stateTransitionError"] = str(getattr(exc, "code", None) or exc)[:240]
    return {"sandboxEvidence": evidence}


def _merge_state_update(state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    if not update:
        return state
    merged = dict(state)
    for key, value in update.items():
        if value is None:
            continue
        if key in {"messages", "todos", "delegation_contexts", "parallel_results", "parallel_invocations", "pending_child_delegations"}:
            merged[key] = list(merged.get(key) or []) + list(value or [])
        elif key == "current_route_context":
            merged[key] = merge_route_context(
                dict(merged.get("current_route_context") or {}),
                dict(value or {}),
            )
        else:
            merged[key] = value
    return merged


def _compact_message_text(message: Any, *, limit: int = 900) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        text = "\n".join(part.strip() for part in parts if part.strip())
    else:
        text = str(content or "")
    text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<think\b[^>]*>.*$", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</?think\b[^>]*>", "", text, flags=re.IGNORECASE)
    visible_lines: list[str] = []
    for line in text.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.search(r"toolobs://|\brawRef\s*:|^Raw:\s*toolobs://|^Detail:\s*tool_observation_detail", stripped, re.IGNORECASE):
            continue
        visible_lines.append(line.rstrip())
    normalized = "\n".join(visible_lines)
    if len(normalized) > limit:
        return normalized[: limit - 3].rstrip() + "..."
    return normalized


_SUBAGENT_TIMELINE_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|encrypted[_-]?content|password|secret|signature|token)",
    re.IGNORECASE,
)


def _sanitize_subagent_timeline_value(value: Any, *, depth: int = 0) -> Any:
    """Keep only bounded, human-usable child activity for the shared timeline."""

    if depth > 5 or value is None:
        return None
    if isinstance(value, str):
        redacted = redact_observability_text(value)
        return redacted if len(redacted) <= 2400 else redacted[:2399].rstrip() + "…"
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_subagent_timeline_value(item, depth=depth + 1)
            for item in list(value)[:32]
        ]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, nested in list(value.items())[:48]:
            normalized_key = str(key)
            output[normalized_key] = (
                "<redacted>"
                if _SUBAGENT_TIMELINE_SECRET_KEY.search(normalized_key)
                else _sanitize_subagent_timeline_value(nested, depth=depth + 1)
            )
        return output
    return _sanitize_subagent_timeline_value(str(value), depth=depth + 1)


def _subagent_timeline_nodes_from_message(message: Any) -> list[dict[str, Any]]:
    """Project a LangChain child message into safe, ordered Human Surface nodes.

    Opaque reasoning continuations and provider metadata are deliberately omitted.
    The original message remains available to the execution runtime; this projection
    is only the compact observation stream used by Web and Phone.
    """

    role = str(getattr(message, "type", None) or getattr(message, "role", None) or "").strip().lower()
    message_id = str(getattr(message, "id", None) or uuid.uuid4().hex).strip()
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    stream_node_ids = (
        dict(additional_kwargs.get("v8_subagent_stream_node_ids") or {})
        if isinstance(additional_kwargs.get("v8_subagent_stream_node_ids"), dict)
        else {}
    )
    nodes: list[dict[str, Any]] = []
    if role in {"ai", "assistant"}:
        visible_text, reasoning = extract_text_and_reasoning(message)
        safe_reasoning = str(_sanitize_subagent_timeline_value(reasoning) or "").strip()
        if safe_reasoning:
            nodes.append(
                {
                    "id": str(stream_node_ids.get("analysis") or f"{message_id}:reasoning"),
                    "kind": "execution",
                    "executionType": "reasoning",
                    "topic": "subagent.reasoning.delta",
                    "content": safe_reasoning,
                    "finalized": True,
                    "partial": False,
                }
            )
        safe_text = str(_sanitize_subagent_timeline_value(visible_text) or "").strip()
        if safe_text:
            nodes.append(
                {
                    "id": str(stream_node_ids.get("text") or f"{message_id}:text"),
                    "kind": "narrative",
                    "role": "assistant",
                    "topic": "subagent.text.delta",
                    "content": safe_text,
                    "finalized": True,
                    "partial": False,
                }
            )
        for ordinal, call in enumerate(_tool_call_dicts_from_message(message)):
            tool_name = str(call.get("name") or "tool").strip() or "tool"
            tool_call_id = str(call.get("id") or f"{message_id}:tool:{ordinal}").strip()
            nodes.append(
                {
                    "id": f"{tool_call_id}:call",
                    "kind": "execution",
                    "executionType": "tool_call",
                    "topic": "subagent.tool.started",
                    "toolName": tool_name,
                    "toolCallId": tool_call_id,
                    "args": _sanitize_subagent_timeline_value(_normalize_tool_call_args(call.get("args"))),
                }
            )
        return nodes
    if role == "tool":
        tool_name = str(getattr(message, "name", None) or "tool").strip() or "tool"
        tool_call_id = str(getattr(message, "tool_call_id", None) or message_id).strip()
        content = getattr(message, "content", "")
        nodes.append(
            {
                "id": f"{message_id}:result",
                "kind": "execution",
                "executionType": "tool_result",
                "topic": "subagent.tool.finished",
                "toolName": tool_name,
                "toolCallId": tool_call_id,
                "agentVisibleResult": _sanitize_subagent_timeline_value(content),
            }
        )
    return nodes


def _subagent_result_summary(messages: list[Any], *, limit: int = 900) -> str:
    for message in reversed(messages):
        role = str(getattr(message, "type", None) or getattr(message, "role", None) or "").strip().lower()
        if role == "tool":
            continue
        candidate = _compact_message_text(message, limit=limit)
        if not candidate or candidate.startswith("[Supervisor Delegated Task"):
            continue
        return candidate
    return ""


def _subagent_result_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        if "v8_subagent_result_text" in additional_kwargs:
            return str(additional_kwargs.get("v8_subagent_result_text") or "").strip()
    for message in reversed(messages):
        role = str(getattr(message, "type", None) or getattr(message, "role", None) or "").strip().lower()
        if role != "ai":
            continue
        candidate = _compact_message_text(message, limit=100_000)
        if candidate:
            return candidate
    return ""


def _subagent_runtime_input_request(
    messages: list[Any],
    *,
    branch: dict[str, Any],
    agent_id: str,
) -> dict[str, Any] | None:
    """Read a typed pause only from the matching broker ToolMessage.

    Prose, final-answer JSON and historical messages are deliberately ignored.
    The tool call id and branch lineage make the pause part of this execution,
    rather than an instruction-shaped string emitted by the model.
    """

    calls_by_id: dict[str, dict[str, Any]] = {}
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    task_context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    for message in messages:
        for call in _tool_call_dicts_from_message(message):
            call_id = str(call.get("id") or "").strip()
            if call_id:
                calls_by_id[call_id] = call
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        call_id = str(getattr(message, "tool_call_id", "") or "").strip()
        call = calls_by_id.get(call_id, {})
        tool_name = str(getattr(message, "name", "") or call.get("name") or "").strip()
        if tool_name != "delegation_broker" or not call_id:
            continue
        try:
            payload = json.loads(str(getattr(message, "content", "") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            continue
        if str(payload.get("mode") or "").strip() != "request_input":
            continue
        request = normalize_runtime_continuation_request(payload.get("continuationRequest"))
        source = dict(request.get("source") or {})
        expected = {
            "runtimeEpisodeId": str(task_context.get("parentRuntimeEpisodeId") or "").strip(),
            "taskBriefId": str(branch.get("taskBriefId") or "").strip(),
            "delegationId": str(branch.get("delegationId") or "").strip(),
            "agentId": str(agent_id or "").strip(),
            "toolCallId": call_id,
        }
        mismatched = [
            key
            for key, value in expected.items()
            if value and str(source.get(key) or "").strip() != value
        ]
        if mismatched:
            raise RuntimeContinuationContractError(
                "runtime_continuation_lineage_mismatch",
                f"Continuation request lineage mismatch: {', '.join(mismatched)}",
            )
        return request
    return None


def _subagent_reported_terminal_failure(result_text: str) -> tuple[str, str] | None:
    """Recognize an explicit final blocker without guessing from ordinary prose."""

    text = str(result_text or "").strip()
    if not text:
        return None
    match = re.search(
        r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?status(?:\*\*)?\s*[:：]\s*"
        r"(?:\*\*)?(blocked|blocker|failed|error)\b",
        text,
    )
    if not match:
        match = re.search(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:执行)?状态(?:\*\*)?\s*[:：]\s*"
            r"(?:\*\*)?(阻塞|失败)\b",
            text,
        )
    if not match:
        section_match = re.search(
            r"(?im)^\s*#{1,6}\s*[^\r\n]*?"
            r"(blockers?|blocked|failures?|failed|errors?|阻塞|失败)"
            r"[^\r\n]*$",
            text,
        )
        if section_match:
            heading = section_match.group(0).strip().lower()
            section_body = text[section_match.end() :]
            next_heading = re.search(r"(?m)^\s*#{1,6}\s+", section_body)
            if next_heading:
                section_body = section_body[: next_heading.start()]
            first_line = next(
                (line.strip() for line in section_body.splitlines() if line.strip()),
                "",
            )
            normalized_first_line = re.sub(r"^[\s>*_`~-]+", "", first_line).strip().lower()
            normalized_first_line = normalized_first_line.replace("**", "").replace("__", "")
            explicitly_empty = bool(
                re.match(
                    r"^(?:(?:blockers?|risks?|errors?|阻塞|风险|错误)\s*[:：]\s*"
                    r"(?:none\b|no\b|n/?a\b|无(?:\s|$)|没有(?:\s|$)|暂无(?:\s|$))|"
                    r"none\b|n/?a\b|not\s+applicable\b|"
                    r"no\s+(?:known\s+)?(?:blockers?|risks?|errors?)\b|"
                    r"无(?:阻塞|风险|错误)?\b|暂无\b|没有\b|未发现\b)",
                    normalized_first_line,
                )
            )
            heading_explicitly_empty = bool(
                re.search(r"\bno\s+(?:known\s+)?(?:blockers?|failures?|errors?)\b", heading)
                or re.search(r"无(?:阻塞|失败|错误)", heading)
            )
            mixed_risk_heading = bool(
                re.search(r"\brisks?\b|notes?|handoff|风险|备注|说明", heading, re.IGNORECASE)
            )
            candidate_lines = (
                [first_line]
                if mixed_risk_heading
                else [line for line in section_body.splitlines() if line.strip()]
            )
            body_reports_terminal_failure = any(
                re.search(
                    r"(?:\b(?:blocked|failed|failure|deferred|unable|unavailable|cannot|"
                    r"could\s+not|did\s+not\s+run|not\s+verified)\b|"
                    r"\bmissing\s+(?:required|expected|artifact|file|evidence|dependency|output)\b|"
                    r"^(?:blocker|error|missing)\s*[:：]|"
                    r"阻断|阻塞|失败|无法|未运行|未验证|未通过|缺失)",
                    re.sub(r"^[\s>*_`~-]+", "", line).strip(),
                    re.IGNORECASE,
                )
                for line in candidate_lines
                if line.strip()
                and not re.search(
                    r"(?:无需|不(?:存在|需要|触发)|未(?:发现|发生|出现)|没有)"
                    r".{0,16}(?:缺失|阻塞|失败|错误)",
                    re.sub(r"^[\s>*_`~-]+", "", line).strip(),
                    re.IGNORECASE,
                )
                and not re.match(
                    r"^[\s>*_`~-]*(?:none\b|n/?a\b|no\s+|无(?:阻塞|风险|错误)?\b|暂无\b|没有\b|未发现\b)",
                    line.strip(),
                    re.IGNORECASE,
                )
            )
            if (
                first_line
                and not explicitly_empty
                and not heading_explicitly_empty
                and body_reports_terminal_failure
            ):
                match = section_match
    if not match:
        match = re.search(
            r"(?im)^\s*(?:\*\*)?(阻断原因|阻塞原因)(?:\*\*)?\s*[:：]",
            text,
        )
    if not match:
        match = re.search(
            r"(?im)^\s*#{1,6}\s*(?:verdict|结论|验收结论)\s*(?:[:：]\s*)?\n?\s*"
            r"(?:\*\*)?(not\s+verified|failed|blocked|未通过|未验证|失败|阻塞)\b",
            text,
        )
    if not match:
        match = re.search(
            r"(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?"
            r"(?:verification|execution)\s+result(?:\*\*)?\s*[:：]\s*"
            r"(?:\*\*)?(blocked|failed|error)\b",
            text,
        )
    if not match:
        match = re.search(
            r"(?im)^\s*\[[^\]\r\n]*(执行被阻断|execution\s+blocked)[^\]\r\n]*\]\s*$",
            text,
        )
    if not match:
        return None
    normalized = str(match.group(1) or "").strip().lower()
    status = (
        "failed"
        if normalized in {"failed", "error", "errors", "not verified", "未通过", "未验证", "失败"}
        else "blocked"
    )
    known_error = next(
        (
            code
            for code in (
                "workspace_not_trusted",
                "workspace_fallback_to_main",
                "workspace_boundary_violation",
                "workspace_command_path_violation",
                "global_skill_mutation_violation",
                "execution_intent_conflict",
                "git_parallel_isolation_required",
            )
            if code in text.lower()
        ),
        (
            "subagent_reported_verification_failure"
            if normalized in {"not verified", "未通过", "未验证"}
            else "subagent_reported_terminal_failure"
        ),
    )
    return status, known_error


def _subagent_governance_terminal_failure(messages: list[Any]) -> tuple[str, str] | None:
    """Read the typed delegation terminal state before considering prose."""

    for message in reversed(messages):
        metadata = getattr(message, "additional_kwargs", None)
        if not isinstance(metadata, dict):
            continue
        if "v8_delegation_status" not in metadata:
            continue
        status = str(metadata.get("v8_delegation_status") or "").strip().lower()
        if status not in {"blocked", "degraded", "failed", "error"}:
            # A later successful terminal wrapper supersedes an earlier
            # correctable blocker from the same branch.
            return None
        error = str(metadata.get("v8_delegation_error") or "").strip()
        return (
            "failed" if status in {"failed", "error"} else "blocked",
            error or "subagent_reported_terminal_failure",
        )
    return None


def _compact_transcript(messages: list[Any], *, limit: int = 1800) -> str:
    chunks: list[str] = []
    for message in messages:
        text = _compact_message_text(message, limit=700)
        tool_names = _extract_tool_names_from_message(message)
        if not text and not tool_names:
            continue
        role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
        if tool_names:
            tool_line = "使用工具: " + ", ".join(tool_names)
            text = f"{tool_line}\n{text}" if text else tool_line
        chunks.append(f"{role}: {text}")
    compact = "\n\n".join(chunks)
    if len(compact) > limit:
        return compact[: limit - 3].rstrip() + "..."
    return compact


def _extract_tool_names_from_message(message: Any) -> list[str]:
    names: list[str] = []

    def _add(value: Any) -> None:
        name = str(value or "").strip()
        if not name:
            return
        if name not in names:
            names.append(name)

    for call in list(getattr(message, "tool_calls", None) or []):
        if isinstance(call, dict):
            _add(call.get("name"))
        else:
            _add(getattr(call, "name", None))

    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict):
        for call in list(additional.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            _add(call.get("name"))
            function = call.get("function")
            if isinstance(function, dict):
                _add(function.get("name"))

    _add(getattr(message, "name", None))
    return names


def _extract_tool_names(messages: list[Any]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for name in _extract_tool_names_from_message(message):
            if name not in names:
                names.append(name)
    return names


def _required_verification_tools(branch: dict[str, Any]) -> set[str]:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    capsule = (
        task_brief.get("engineeringTaskCapsule")
        if isinstance(task_brief.get("engineeringTaskCapsule"), dict)
        else context.get("engineeringExecutionContract")
        if isinstance(context.get("engineeringExecutionContract"), dict)
        else {}
    )
    mode = str(capsule.get("executionMode") or capsule.get("execution_mode") or "").strip().lower()
    if mode != "verify" and not bool(task_brief.get("readOnly") or task_brief.get("read_only")):
        return set()
    required: set[str] = set()
    must_read = list(capsule.get("mustRead") or capsule.get("readSet") or task_brief.get("readSet") or [])
    if any(str(item or "").strip() for item in must_read):
        required.add("read_native_file")
    contract_blob = "\n".join(
        _stringify_for_acceptance(value)
        for value in (
            task_brief.get("acceptanceTiers"),
            task_brief.get("acceptanceContract"),
            task_brief.get("expectedOutputs"),
            capsule.get("acceptance"),
            capsule.get("expectedOutputs"),
            capsule.get("verificationContract"),
        )
    ).lower()
    execution_requested = any(
        marker in contract_blob
        for marker in (
            "实际执行",
            "执行退出码",
            "stdout",
            "stderr",
            "run the command",
            "execute the",
            "exit code",
        )
    )
    explicit_execution_with_read = any(
        marker in contract_blob
        for marker in (
            "实际执行",
            "执行退出码",
            "stdout",
            "stderr",
            "run the command",
            "execute the",
        )
    )
    if execution_requested and (
        not any(str(item or "").strip() for item in must_read)
        or explicit_execution_with_read
    ):
        required.add("run_system_command")
    return required


def _tool_message_evidence_succeeded(message: Any, *, tool_name: str) -> bool:
    if not isinstance(message, ToolMessage):
        return False
    if str(getattr(message, "name", "") or "").strip() != tool_name:
        return False
    content = str(getattr(message, "content", "") or "").strip()
    if not content:
        return False
    try:
        payload = json.loads(content)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            return False
        if tool_name == "run_system_command":
            try:
                return_code = int(payload.get("returnCode", payload.get("return_code", -1)))
            except (TypeError, ValueError):
                return False
            return (
                payload.get("ok") is True
                and str(payload.get("kind") or "").strip() == "command_result"
                and return_code == 0
            )
        return payload.get("ok") is not False
    lowered = content.lower()
    if any(
        marker in lowered
        for marker in (
            "error:",
            "[command_session_required]",
            "[completed with no output]",
            "[git_parallel_isolation_required]",
            "not a valid tool",
        )
    ):
        return False
    if tool_name == "run_system_command":
        # Agent-visible command results are intentionally rendered as a small
        # terminal transcript instead of raw JSON. A leading command plus a
        # stdout/stderr envelope is the successful zero-exit surface; non-zero
        # results carry an explicit exit-code marker and were rejected above.
        return bool(
            re.search(r"(?m)^\$\s+\S", content)
            and ("<stdout>" in lowered or "<stderr>" in lowered or "[completed with no output]" in lowered)
            and not re.search(r"(?im)^\[exit code:\s*[1-9]\d*\]", content)
        )
    return True


def _tool_execution_records(messages: list[Any]) -> list[dict[str, Any]]:
    calls_by_id: dict[str, dict[str, Any]] = {}
    for message in messages:
        for call in _tool_call_dicts_from_message(message):
            call_id = str(call.get("id") or "").strip()
            if call_id:
                calls_by_id[call_id] = call

    records: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        call_id = str(getattr(message, "tool_call_id", "") or "").strip()
        call = calls_by_id.get(call_id, {})
        tool_name = str(getattr(message, "name", "") or call.get("name") or "").strip()
        args = _normalize_tool_call_args(call.get("args"))
        content = str(getattr(message, "content", "") or "").strip()
        try:
            payload = json.loads(content)
        except Exception:
            payload = None
        payload = payload if isinstance(payload, dict) else {}
        command = str(args.get("command") or payload.get("command") or "").strip()
        if not command:
            command_match = re.search(r"(?m)^\$\s+(.+?)\s*$", content)
            command = str(command_match.group(1) if command_match else "").strip()
        path = str(args.get("path") or payload.get("path") or "").strip()
        if not path:
            path_match = re.search(r"(?m)^---\s*File:\s*(.+?)(?:\s*\(Lines?\b.*)?\s*---$", content)
            path = str(path_match.group(1) if path_match else "").strip()
        stdout = str(payload.get("keyOutput") or payload.get("stdout") or payload.get("stdoutPreview") or "")
        stderr = str(payload.get("keyErrors") or payload.get("stderr") or payload.get("stderrPreview") or "")
        if not payload:
            stdout_match = re.search(r"<stdout>\s*\n?(.*?)\n?\s*</stdout>", content, re.DOTALL | re.IGNORECASE)
            stderr_match = re.search(r"<stderr>\s*\n?(.*?)\n?\s*</stderr>", content, re.DOTALL | re.IGNORECASE)
            stdout = str(stdout_match.group(1) if stdout_match else "")
            stderr = str(stderr_match.group(1) if stderr_match else "")
        return_code: int | None = None
        raw_return_code = payload.get("returnCode", payload.get("return_code"))
        if raw_return_code is not None:
            try:
                return_code = int(raw_return_code)
            except (TypeError, ValueError):
                return_code = None
        elif tool_name == "run_system_command" and _tool_message_evidence_succeeded(message, tool_name=tool_name):
            return_code = 0
        records.append(
            {
                "toolCallId": call_id,
                "callMatched": bool(call_id and call_id in calls_by_id),
                "tool": tool_name,
                "args": args,
                "path": path,
                "command": command,
                "returnCode": return_code,
                "stdout": stdout,
                "stderr": stderr,
                "payload": payload,
                "succeeded": _tool_message_evidence_succeeded(message, tool_name=tool_name),
            }
        )
    return records


def _normalized_tool_surface_name(value: Any) -> str:
    name = str(value or "").strip()
    if name.startswith("gateway."):
        name = name[len("gateway.") :].strip()
    if "." in name:
        name = name.split(".", 1)[1].strip()
    return name


def _available_tool_surface(agent_data: dict[str, Any] | None) -> list[str]:
    names: set[str] = set()
    for tool_ref in list((agent_data or {}).get("tools") or []):
        raw_name = getattr(tool_ref, "name", None) if not isinstance(tool_ref, str) else tool_ref
        normalized = _normalized_tool_surface_name(raw_name)
        if normalized:
            names.add(normalized)
    return sorted(names)


def _required_artifact_write_status(
    *,
    branch: dict[str, Any],
    state: dict[str, Any],
    delta_messages: list[Any],
    agent_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return bounded write evidence for a write-required delegated branch."""
    expected_paths = _infer_expected_artifact_paths(branch, state)
    if not expected_paths:
        return None
    records = _tool_execution_records(delta_messages)
    write_records = [
        record
        for record in records
        if _normalized_tool_surface_name(record.get("tool")) == "write_native_file"
    ]
    available_tools = _available_tool_surface(agent_data)
    missing_paths = [str(path) for path in expected_paths if not path.exists()]
    write_tool_succeeded = any(bool(record.get("succeeded")) for record in write_records)
    return {
        "requiredTool": "write_native_file",
        "requiredToolVisible": "write_native_file" in available_tools,
        "requiredToolChoice": (
            "required"
            if "write_native_file" in available_tools and not write_tool_succeeded
            else None
        ),
        "availableTools": available_tools,
        "toolCallCount": len(records),
        "writeToolCallCount": len(write_records),
        "writeToolSucceeded": write_tool_succeeded,
        "missingRequiredTool": (
            "write_native_file"
            if "write_native_file" not in available_tools or not write_tool_succeeded
            else None
        ),
        "expectedArtifacts": [str(path) for path in expected_paths],
        "missingExpectedArtifacts": missing_paths,
    }


_CREATIVE_FACADE_BY_TOOL = {
    "creative_media_capabilities": "capabilities",
    "creative_media_plan": "plan",
    "creative_media_assets": "assets",
    "creative_media_jobs": "jobs",
    "creative_media_edit": "edit",
    "creative_media_quality": "quality",
}
_CREATIVE_ARTIFACT_ACTIONS = {
    ("jobs", "artifacts"),
    ("edit", "get_render"),
    ("assets", "register_asset"),
    ("assets", "register_keyframe"),
    ("assets", "psd_compose_template"),
    ("quality", "psd_export_preview"),
}
_CREATIVE_PROOF_ACTIONS = {
    ("quality", "get_job"),
    ("quality", "qa_check"),
    ("quality", "alpha_inspect"),
    ("quality", "image_compare"),
    ("quality", "psd_export_preview"),
}
_CREATIVE_INCOMPLETE_STATUSES = {
    "created",
    "pending",
    "queued",
    "running",
    "processing",
    "submitted",
    "waiting",
}


def _is_creative_runtime_execution_branch(branch: dict[str, Any]) -> bool:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    runtime_access = {
        str(item or "").strip().lower()
        for item in list(task_brief.get("runtimeAccess") or [])
        if str(item or "").strip()
    }
    capabilities = {
        str(item or "").strip().lower()
        for item in list(task_brief.get("requiredCapabilities") or [])
        if str(item or "").strip()
    }
    return bool(
        "creative_media.core" in runtime_access
        and str(context.get("parentRuntimeEpisodeId") or "").strip()
        and {"artifact_handoff", "quality_assurance"}.issubset(capabilities)
    )


def _creative_tool_evidence(
    messages: list[Any],
    *,
    branch: dict[str, Any],
) -> dict[str, Any]:
    """Project only successful, call-matched Creative facade ToolMessages.

    The compact facade payload remains Agent-facing. This projection retains
    the tool-call lineage needed by the runtime handoff without accepting IDs
    guessed from prose or recursively scraping unrelated runtime metadata.
    """

    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    records: list[dict[str, Any]] = []
    artifact_refs: list[str] = []
    proof_refs: list[str] = []
    for record in _tool_execution_records(messages):
        tool_name = str(record.get("tool") or "").strip()
        expected_facade = _CREATIVE_FACADE_BY_TOOL.get(tool_name)
        if not expected_facade or not record.get("callMatched") or not record.get("succeeded"):
            continue
        tool_call_id = str(record.get("toolCallId") or "").strip()
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        args = record.get("args") if isinstance(record.get("args"), dict) else {}
        facade = str(payload.get("facade") or "").strip()
        action = str(payload.get("action") or "").strip()
        requested_action = str(args.get("action") or "").strip()
        status = str(payload.get("status") or "").strip().lower()
        if (
            not tool_call_id
            or payload.get("ok") is not True
            or facade != expected_facade
            or not action
            or (requested_action and requested_action != action)
        ):
            continue
        refs = list(
            dict.fromkeys(
                str(ref).strip()
                for ref in list(payload.get("refs") or [])
                if isinstance(ref, (str, int, float)) and str(ref).strip()
            )
        )[:24]
        detail_ref = str(payload.get("detailRef") or "").strip()
        lineage_record = {
            "toolCallId": tool_call_id,
            "tool": tool_name,
            "facade": facade,
            "action": action,
            "status": status or "succeeded",
            "refs": refs,
            **({"detailRef": detail_ref} if detail_ref else {}),
            "summary": str(payload.get("summary") or "").strip()[:600],
        }
        records.append(lineage_record)
        if status in _CREATIVE_INCOMPLETE_STATUSES:
            continue
        if (facade, action) in _CREATIVE_ARTIFACT_ACTIONS and refs:
            artifact_refs.extend(refs)
        if (facade, action) in _CREATIVE_PROOF_ACTIONS:
            proof_refs.extend(refs)
            if detail_ref:
                proof_refs.append(detail_ref)

    artifact_refs = list(dict.fromkeys(artifact_refs))[:24]
    proof_refs = list(dict.fromkeys(proof_refs))[:24]
    missing = [
        label
        for label, values in (("artifactRefs", artifact_refs), ("proofRefs", proof_refs))
        if not values
    ]
    return {
        "schemaVersion": "creative-execution-evidence/v1",
        "sourceRuntimeEpisodeId": str(context.get("parentRuntimeEpisodeId") or "").strip(),
        "taskBriefId": str(branch.get("taskBriefId") or task_brief.get("taskBriefId") or "").strip(),
        "delegationId": str(branch.get("delegationId") or "").strip(),
        "records": records[-32:],
        "artifactRefs": artifact_refs,
        "proofRefs": proof_refs,
        "missingEvidence": missing,
    }


def _verification_contract_sources(branch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    capsule = (
        task_brief.get("engineeringTaskCapsule")
        if isinstance(task_brief.get("engineeringTaskCapsule"), dict)
        else context.get("engineeringExecutionContract")
        if isinstance(context.get("engineeringExecutionContract"), dict)
        else {}
    )
    return task_brief, context, capsule


def _verification_command_text(value: Any) -> str:
    """Separate a declared command from the prose that describes its result."""

    command = str(value or "").strip().strip("`'\"")
    if not command:
        return ""
    result_boundary = re.search(
        r"(?:"
        r"\s*(?:后|的)(?=\s*(?:stdout|stderr|标准输出|标准错误|退出码|返回码))|"
        r"\s+(?:and|then|where|with)\s+(?=(?:the\s+)?(?:command\s+)?(?:stdout|stderr|exit\s+code|return\s+code))|"
        r"\s+(?=(?:stdout|stderr|exit\s+code|return\s+code)\b)"
        r")",
        command,
        re.IGNORECASE,
    )
    if result_boundary:
        command = command[: result_boundary.start()]
    return command.strip().rstrip("。.!?").strip().strip("`'\"")


def _verification_command_matches_exact(command: Any, required_commands: list[Any]) -> bool:
    normalized = re.sub(r"\s+", " ", str(command or "").strip().replace("\\", "/")).casefold()
    if not normalized:
        return False
    required = {
        re.sub(r"\s+", " ", str(item or "").strip().replace("\\", "/")).casefold()
        for item in required_commands
        if str(item or "").strip()
    }
    return normalized in required


def _normalize_exact_verification_command_invocations(
    messages: list[Any],
    required_commands: list[Any],
) -> list[dict[str, Any]]:
    """Normalize a bounded exact command before ToolNode starts a process."""

    adjustments: list[dict[str, Any]] = []
    if not required_commands:
        return adjustments
    for message in messages:
        calls = getattr(message, "tool_calls", None)
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict) or str(call.get("name") or "").strip() != "run_system_command":
                continue
            args = _normalize_tool_call_args(call.get("args"))
            command = str(args.get("command") or "").strip()
            if not _verification_command_matches_exact(command, required_commands):
                continue
            mode = str(args.get("mode") or "auto").strip().lower()
            raw_timeout = args.get("timeout_seconds", args.get("timeoutSeconds"))
            try:
                timeout_seconds = float(raw_timeout) if raw_timeout not in (None, "") else 90.0
            except (TypeError, ValueError):
                timeout_seconds = 90.0
            if mode == "sync" and 0 < timeout_seconds <= 90:
                continue
            normalized_args = dict(args)
            normalized_args["mode"] = "sync"
            normalized_args["timeout_seconds"] = max(1, min(int(timeout_seconds or 90), 90))
            normalized_args.pop("timeoutSeconds", None)
            call["args"] = normalized_args
            adjustments.append(
                {
                    "toolCallId": str(call.get("id") or "").strip(),
                    "command": command,
                    "fromMode": mode or "auto",
                    "fromTimeoutSeconds": raw_timeout,
                    "toMode": "sync",
                    "toTimeoutSeconds": normalized_args["timeout_seconds"],
                }
            )
    return adjustments


def _verification_command_preflight_deviations(
    messages: list[Any],
    required_commands: list[Any],
) -> list[dict[str, Any]]:
    deviations: list[dict[str, Any]] = []
    if not required_commands:
        return deviations
    for message in messages:
        for call in _tool_call_dicts_from_message(message):
            if str(call.get("name") or "").strip() != "run_system_command":
                continue
            args = _normalize_tool_call_args(call.get("args"))
            command = str(args.get("command") or "").strip()
            if command and not _verification_command_matches_exact(command, required_commands):
                deviations.append(
                    {
                        "toolCallId": str(call.get("id") or "").strip(),
                        "command": command,
                    }
                )
    return deviations


def _verification_declared_path(value: Any) -> str:
    """Normalize common task-brief labels without treating them as paths."""

    text = str(value or "").strip().strip("`'\"")
    if re.match(
        r"^(?:verification[_ -]?command|command|expected[_ -]?(?:stdout|stderr|exit[_ -]?code|return[_ -]?code))\s*[:=]",
        text,
        re.IGNORECASE,
    ):
        return ""
    labeled = re.match(
        r"^(?:target[_ -]?file|source[_ -]?file|file|path|target)\s*[:=]\s*(.+)$",
        text,
        re.IGNORECASE,
    )
    return str(labeled.group(1) if labeled else text).strip().strip("`'\"")


def _verification_expectations(branch: dict[str, Any]) -> dict[str, Any]:
    task_brief, context, capsule = _verification_contract_sources(branch)
    explicit = next(
        (
            dict(value)
            for value in (
                task_brief.get("verificationEvidenceContract"),
                context.get("verificationEvidenceContract"),
                capsule.get("verificationEvidenceContract"),
            )
            if isinstance(value, dict)
        ),
        {},
    )

    def _texts(value: Any) -> list[str]:
        values = value if isinstance(value, (list, tuple, set)) else [value]
        return list(dict.fromkeys(str(item or "").strip() for item in values if str(item or "").strip()))

    declared_read_values = _texts(
        explicit.get("requiredReadPaths")
        or capsule.get("mustRead")
        or capsule.get("readSet")
        or task_brief.get("readSet")
    )
    read_paths = list(
        dict.fromkeys(
            path
            for item in declared_read_values
            if (path := _verification_declared_path(item))
        )
    )
    contract_values = (
        task_brief.get("acceptanceTiers"),
        task_brief.get("acceptanceContract"),
        task_brief.get("expectedOutputs"),
        task_brief.get("proofExpectations"),
        capsule.get("acceptance"),
        capsule.get("expectedOutputs"),
        capsule.get("verificationContract"),
    )
    contract_blob = "\n".join(_stringify_for_acceptance(value) for value in contract_values)
    required_commands = _texts(explicit.get("requiredCommands"))
    for source in (task_brief, context, capsule):
        for key in ("verificationCommand", "verification_command", "requiredCommands", "required_commands"):
            required_commands.extend(
                command
                for value in _texts(source.get(key))
                if (command := _verification_command_text(value))
            )
    required_commands.extend(
        command
        for item in declared_read_values
        for match in [
            re.match(
                r"^verification[_ -]?command\s*[:=]\s*(.+)$",
                str(item or "").strip(),
                re.IGNORECASE,
            )
        ]
        if match and (command := _verification_command_text(match.group(1)))
    )
    required_commands = list(dict.fromkeys(required_commands))
    if not required_commands:
        command_pattern = re.compile(
            r"(?:执行|运行|execute|executing|run|running)\s+"
            r"(?:(?:via|using)\s+)?(?:命令\s*)?[`'\"]?"
            r"((?:python(?:3)?|pytest|npm|pnpm|yarn|npx|node|bun|deno|go|cargo|dotnet|gradle|mvn)\b"
            r"[^，,；;。\n\]\)）`\"']*)",
            re.IGNORECASE,
        )
        required_commands = list(
            dict.fromkeys(
                command
                for match in command_pattern.finditer(contract_blob)
                if (command := _verification_command_text(match.group(1)))
            )
        )
    if not required_commands:
        command_first_pattern = re.compile(
            r"(?:^|[\n\"'`\[,])\s*[`'\"]?"
            r"((?:python(?:3)?|pytest|npm|pnpm|yarn|npx|node|bun|deno|go|cargo|dotnet|gradle|mvn)\b"
            r"[^，,；;。\n\]\)）`\"']*?)\s+"
            r"(?:(?:is|was|must\s+be)\s+)?"
            r"(?:executed|invoked|run|passes?|succeeds?|returns?|exits?|exit\s+code)\b",
            re.IGNORECASE,
        )
        required_commands = list(
            dict.fromkeys(
                command
                for match in command_first_pattern.finditer(contract_blob)
                if (command := _verification_command_text(match.group(1)))
            )
        )
    expected_stdout = _texts(explicit.get("expectedStdout"))
    if not expected_stdout:
        stdout_comparison = (
            r"(?:严格(?:等于|为)|精确(?:等于|为)|等于|为|"
            r"\b(?:"
            r"(?:must\s+)(?:strictly\s+|exactly\s+)?equal|"
            r"(?:strictly\s+|exactly\s+)?equal\s+to|"
            r"(?:must\s+)?(?:strictly\s+|exactly\s+)?(?:equals|is|be)"
            r")\b)"
        )
        stdout_filler = (
            r"(?:(?:the\s+)?(?:(?:exact|literal)\s+)?"
            r"(?:byte\s+sequence|string|text|value|literal|output)\s*)?"
        )
        quoted_stdout_pattern = re.compile(
            r"\bstdout\b[^\n，,；;]{0,80}?"
            + stdout_comparison
            + r"\s*(?:exactly\s*)?[:=]?\s*"
            + stdout_filler
            + r"[:=]?\s*[`'\"]([^`'\"\r\n]+)[`'\"]",
            re.IGNORECASE,
        )
        expected_stdout = list(
            dict.fromkeys(match.group(1).strip() for match in quoted_stdout_pattern.finditer(contract_blob))
        )
    if not expected_stdout:
        unquoted_stdout_pattern = re.compile(
            r"\bstdout\b[^\n，,；;]{0,56}?(?:"
            + stdout_comparison
            + r")\s*(?:exactly\s*)?[:=]?\s*"
            + stdout_filler
            + r"[:=]?\s*"
            r"([A-Za-z0-9_.:/-]+)",
            re.IGNORECASE,
        )
        grammar_fillers = {"the", "exact", "string", "text", "value", "literal", "output", "byte", "sequence"}
        expected_stdout = list(
            dict.fromkeys(
                value
                for match in unquoted_stdout_pattern.finditer(contract_blob)
                if (value := match.group(1).strip()) and value.casefold() not in grammar_fillers
            )
        )
    expect_empty_stderr = bool(explicit.get("expectEmptyStderr")) or bool(
        re.search(r"stderr[^\n，,；;]{0,32}(?:为空|empty|blank|must\s+be\s+empty)", contract_blob, re.IGNORECASE)
    )
    command_targets = _texts(explicit.get("requiredCommandTargets"))
    required_tools = _required_verification_tools(branch)
    if required_commands or command_targets:
        required_tools.add("run_system_command")
    if "run_system_command" in required_tools and not required_commands:
        command_targets = command_targets or read_paths
    return {
        "requiredTools": sorted(required_tools),
        "requiredReadPaths": read_paths,
        "requiredCommands": required_commands,
        "requiredCommandTargets": command_targets,
        "expectedStdout": expected_stdout,
        "expectEmptyStderr": expect_empty_stderr,
    }


def _normalized_evidence_path(value: Any) -> str:
    text = str(value or "").strip().strip("`'\"").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return re.sub(r"/+", "/", text).casefold()


def _verification_evidence_result(
    *,
    branch: dict[str, Any],
    delta_messages: list[Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    expectations = _verification_expectations(branch)
    records = _tool_execution_records(delta_messages)
    missing_tools: list[str] = []
    mismatches: list[str] = []
    successful_by_tool = {
        tool: [record for record in records if record.get("tool") == tool and record.get("succeeded")]
        for tool in expectations["requiredTools"]
    }
    for tool_name in expectations["requiredTools"]:
        if not successful_by_tool.get(tool_name):
            missing_tools.append(tool_name)

    required_read_paths = [_normalized_evidence_path(item) for item in expectations["requiredReadPaths"]]
    observed_read_paths = [
        _normalized_evidence_path(record.get("path"))
        for record in successful_by_tool.get("read_native_file", [])
        if _normalized_evidence_path(record.get("path"))
    ]
    for required_path in required_read_paths:
        if not any(observed == required_path or observed.endswith("/" + required_path) for observed in observed_read_paths):
            mismatches.append(f"read_path_not_verified:{required_path}")

    command_records = successful_by_tool.get("run_system_command", [])
    normalized_commands = [
        re.sub(r"\s+", " ", str(record.get("command") or "").strip().replace("\\", "/")).casefold()
        for record in command_records
    ]
    for required_command in expectations["requiredCommands"]:
        normalized_required = re.sub(r"\s+", " ", str(required_command).replace("\\", "/")).casefold()
        if normalized_required and not any(normalized_required in command for command in normalized_commands):
            mismatches.append(f"required_command_not_executed:{required_command}")
    for target in expectations["requiredCommandTargets"]:
        normalized_target = _normalized_evidence_path(target)
        if normalized_target and not any(normalized_target in command for command in normalized_commands):
            mismatches.append(f"command_target_not_executed:{target}")

    expected_stdout = list(expectations["expectedStdout"])
    if expected_stdout and command_records:
        observed_stdout = [str(record.get("stdout") or "").strip() for record in command_records]
        for expected in expected_stdout:
            if str(expected).strip() not in observed_stdout:
                mismatches.append(f"stdout_mismatch:expected={expected}")
    if expectations["expectEmptyStderr"] and command_records:
        if not any(not str(record.get("stderr") or "").strip() for record in command_records):
            mismatches.append("stderr_not_empty")

    compact_records = [
        {
            key: record.get(key)
            for key in ("toolCallId", "tool", "path", "command", "returnCode", "stdout", "stderr")
            if record.get(key) not in (None, "", [], {})
        }
        for record in records
        if record.get("tool") in set(expectations["requiredTools"])
    ]
    evidence = {
        "passed": not missing_tools and not mismatches,
        "expectations": expectations,
        "observations": compact_records[:12],
    }
    return evidence, missing_tools, mismatches


def _validate_required_verification_evidence(
    *,
    branch: dict[str, Any],
    delta_messages: list[Any],
) -> dict[str, Any] | None:
    required_tools = set(_verification_expectations(branch)["requiredTools"])
    if not required_tools:
        return None
    _evidence, missing, mismatches = _verification_evidence_result(
        branch=branch,
        delta_messages=delta_messages,
    )
    if not missing and not mismatches:
        return None
    return {
        "status": "failed",
        "error": "verification_evidence_missing" if missing else "verification_evidence_mismatch",
        "missingVerificationTools": missing,
        "verificationEvidenceMismatches": mismatches,
        "localSelfCheck": (
            "This verification worker returned without tool evidence that semantically matches its execution "
            "contract. A successful unrelated command, a tool name, or a prose claim is not proof."
        ),
        "acceptanceHint": "Retry the verification worker against the exact declared paths, commands, and outputs before acceptance.",
    }


def _tool_call_dicts_from_message(message: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for call in list(getattr(message, "tool_calls", None) or []):
        if isinstance(call, dict):
            calls.append(dict(call))
            continue
        calls.append(
            {
                "id": getattr(call, "id", None),
                "name": getattr(call, "name", None),
                "args": getattr(call, "args", None),
            }
        )
    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict):
        for call in list(additional.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            args: Any = {}
            name = call.get("name")
            if isinstance(function, dict):
                name = function.get("name") or name
                args = function.get("arguments") or {}
            calls.append({"id": call.get("id"), "name": name, "args": args})
    return calls


def _normalize_tool_call_args(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _repeat_sensitive_tool_call_signature(call: dict[str, Any]) -> tuple[str, str] | None:
    name = str(call.get("name") or "").strip()
    if not name:
        return None
    args = _normalize_tool_call_args(call.get("args"))
    if name in {"run_system_command", "start_background_command"}:
        command = re.sub(r"\s+", " ", str(args.get("command") or "").strip())
        if command:
            lowered = command.lower()
            if any(
                marker in lowered
                for marker in (
                    "research_evidence_bundle",
                    "engineering_patch_bundle",
                    "research://",
                    "engineering://",
                    "episode_",
                )
            ):
                return "runtime_handoff_lookup", "runtime_handoff_identifier_is_not_a_file"
            return name, command.lower()
    if name == "read_native_file":
        path = str(args.get("path") or "").strip()
        start_line = str(args.get("start_line") or args.get("startLine") or "").strip()
        end_line = str(args.get("end_line") or args.get("endLine") or "").strip()
        if path:
            lowered = path.lower()
            if any(
                marker in lowered
                for marker in (
                    "research_evidence_bundle",
                    "engineering_patch_bundle",
                    "research://",
                    "engineering://",
                    "episode_",
                )
            ):
                return "runtime_handoff_lookup", "runtime_handoff_identifier_is_not_a_file"
            return name, "|".join([path.lower(), start_line, end_line])
    if name in {
        "creative_media_capabilities",
        "creative_media_plan",
        "creative_media_assets",
        "creative_media_jobs",
        "creative_media_edit",
        "creative_media_quality",
    }:
        action = str(args.get("action") or "").strip().lower()
        if name == "creative_media_jobs" and action in {"get", "list", "artifacts", "cancel"}:
            return None
        canonical_args = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return name, canonical_args[:2400]
    return None


def _stringify_for_acceptance(value: Any, *, limit: int = 12000) -> str:
    try:
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            parts: list[str] = []
            for key, item in value.items():
                parts.append(f"{key}: {_stringify_for_acceptance(item, limit=2000)}")
            text = "\n".join(parts)
        elif isinstance(value, (list, tuple, set)):
            text = "\n".join(_stringify_for_acceptance(item, limit=2000) for item in value)
        else:
            text = str(value or "")
    except Exception:
        text = str(value or "")
    return text[:limit]


def _branch_requires_skill_artifact_validation(branch: dict[str, Any]) -> bool:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    if bool(task_brief.get("validateSkillArtifact") or task_brief.get("validate_skill_artifact")):
        return True
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    if bool(context.get("validateSkillArtifact") or context.get("validate_skill_artifact")):
        return True
    task_id = str(
        branch.get("taskBriefId")
        or task_brief.get("taskBriefId")
        or task_brief.get("taskId")
        or context.get("taskId")
        or ""
    ).strip().upper()
    deliverable = str(task_brief.get("deliverableKind") or task_brief.get("deliverable_kind") or "").strip().lower()
    if deliverable == "skill_artifact":
        return True
    blob = "\n".join(
        _stringify_for_acceptance(value)
        for value in (
            task_id,
            branch.get("reason"),
            branch.get("taskGoal"),
            task_brief.get("title"),
            task_brief.get("goal"),
            task_brief.get("acceptanceContract"),
            context.get("artifactAcceptanceGuard"),
            context.get("expectedOutputs"),
        )
    ).lower()
    if "skill.md" not in blob and "skill artifact" not in blob and "skill_artifact" not in blob:
        return False
    artifact_stage_markers = (
        "组装",
        "构建",
        "生成",
        "写入",
        "创建完整",
        "质量验证",
        "交付前质量验证",
        "build",
        "assemble",
        "write",
        "validate",
    )
    if task_id in {"TASK-010", "TASK-011"}:
        return True
    return any(marker in blob for marker in artifact_stage_markers)


def _parallel_branch_progress_fingerprint(
    *,
    current_node: str,
    state: dict[str, Any],
    initial_message_count: int,
    initial_todo_count: int,
) -> str:
    messages = list(state.get("messages") or [])[initial_message_count:]
    todos = list(state.get("todos") or [])[initial_todo_count:]
    recent_messages: list[dict[str, Any]] = []
    for message in messages[-4:]:
        recent_messages.append(
            {
                "role": str(getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__),
                "name": str(getattr(message, "name", None) or ""),
                "content": _compact_message_text(message, limit=1200),
                "tools": _extract_tool_names_from_message(message),
            }
        )
    payload = {
        "node": current_node,
        "recentMessages": recent_messages,
        "recentTodos": todos[-4:],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _workspace_path_from_state(state: dict[str, Any]) -> Path | None:
    workspace = str(
        state.get("workspace_path")
        or state.get("workspacePath")
        or (state.get("current_route_context") or {}).get("workspace_path")
        or (state.get("current_route_context") or {}).get("workspacePath")
        or ""
    ).strip()
    if not workspace:
        return None
    try:
        return Path(workspace).expanduser().resolve()
    except Exception:
        return None


def _collect_expected_artifact_values(branch: dict[str, Any]) -> list[Any]:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
    contract = context.get("engineeringExecutionContract") if isinstance(context.get("engineeringExecutionContract"), dict) else {}
    values: list[Any] = []
    for source in (task_brief, capsule, contract):
        for key in ("expectedArtifacts", "expected_artifacts"):
            raw = source.get(key)
            if isinstance(raw, str):
                values.append(raw)
            else:
                values.extend(list(raw or []))
    return values


def _infer_expected_artifact_paths(branch: dict[str, Any], state: dict[str, Any]) -> list[Path]:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
    contract = context.get("engineeringExecutionContract") if isinstance(context.get("engineeringExecutionContract"), dict) else {}
    write_required = any(
        bool(source.get("writeRequired") or source.get("write_required"))
        for source in (task_brief, capsule, contract)
        if isinstance(source, dict)
    )
    if not write_required:
        return []
    workspace = _workspace_path_from_state(state)
    if not workspace:
        return []
    paths: list[Path] = []
    for raw_value in _collect_expected_artifact_values(branch):
        if isinstance(raw_value, dict):
            raw_value = raw_value.get("path") or raw_value.get("filePath") or raw_value.get("file_path")
        candidate = str(raw_value or "").strip().strip("`'\"，,。;；")
        if (
            not candidate
            or candidate.startswith(("spec://", "http://", "https://", "file://"))
            or any(marker in candidate for marker in ("<", ">", "\r", "\n"))
        ):
            continue
        path = Path(candidate)
        resolved = path if path.is_absolute() else workspace / path
        try:
            resolved = resolved.expanduser().resolve()
            resolved.relative_to(workspace)
        except Exception:
            continue
        if resolved not in paths:
            paths.append(resolved)
    return paths[:16]


def _artifact_progress_snapshot(paths: list[Path]) -> tuple[tuple[str, bool, bool, int, int], ...]:
    snapshot: list[tuple[str, bool, bool, int, int]] = []
    for path in paths:
        try:
            exists = path.exists()
            is_dir = path.is_dir() if exists else False
            if exists and is_dir:
                try:
                    child_count = sum(1 for _ in path.rglob("*"))
                except Exception:
                    child_count = 0
                stat = path.stat()
                snapshot.append((str(path), True, True, child_count, int(stat.st_mtime_ns)))
            elif exists:
                stat = path.stat()
                snapshot.append((str(path), True, False, int(stat.st_size), int(stat.st_mtime_ns)))
            else:
                snapshot.append((str(path), False, False, 0, 0))
        except Exception:
            snapshot.append((str(path), False, False, 0, 0))
    return tuple(snapshot)


def _infer_required_skill_artifacts(branch: dict[str, Any], state: dict[str, Any]) -> list[Path]:
    if not _branch_requires_skill_artifact_validation(branch):
        return []
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    blob = "\n".join(
        part
        for part in [
            _stringify_for_acceptance(branch.get("reason")),
            _stringify_for_acceptance(branch.get("taskGoal")),
            _stringify_for_acceptance(task_brief),
        ]
        if part.strip()
    )
    if ".agents" not in blob or "skills" not in blob:
        return []
    skill_root_match = re.search(
        r"([A-Za-z]:[\\/][^\r\n\"'<>|]*?\.agents[\\/]skills[\\/][^\s\r\n\"'<>|，。；;]+)",
        blob,
    )
    base_dir: Path | None = None
    if skill_root_match:
        raw_path = skill_root_match.group(1).rstrip(".,，。；;:：")
        base_dir = Path(raw_path)
        if base_dir.name.lower() == "skill.md":
            base_dir = base_dir.parent
    else:
        workspace = str(
            state.get("workspace_path")
            or state.get("workspacePath")
            or (state.get("current_route_context") or {}).get("workspace_path")
            or (state.get("current_route_context") or {}).get("workspacePath")
            or ""
        ).strip()
        slug_match = re.search(r"skill[s]?[\\/](?P<slug>[A-Za-z0-9_.-]+)", blob)
        if workspace and slug_match:
            base_dir = Path(workspace) / ".agents" / "skills" / slug_match.group("slug")
    if not base_dir:
        return []
    required = [base_dir / "SKILL.md"]
    if "huashu-nuwa" in blob or "01-writings" in blob or "references/research" in blob.replace("\\", "/"):
        required.extend(
            [
                base_dir / "references" / "research" / "01-writings.md",
                base_dir / "references" / "research" / "02-conversations.md",
                base_dir / "references" / "research" / "03-expression-dna.md",
                base_dir / "references" / "research" / "04-external-views.md",
                base_dir / "references" / "research" / "05-decisions.md",
                base_dir / "references" / "research" / "06-timeline.md",
            ]
        )
    return required


def _validate_required_skill_artifacts(
    *,
    branch: dict[str, Any],
    state: dict[str, Any],
    delta_messages: list[Any],
) -> dict[str, Any] | None:
    required = _infer_required_skill_artifacts(branch, state)
    if not required:
        return None
    requires_huashu_research = any("references" in str(path).replace("\\", "/") and "research" in str(path).replace("\\", "/") for path in required)
    placeholder_re = re.compile(
        r"(待调研|待补充|待填充|占位|空目录|空模板|placeholder|todo|tbd|无官方设定来源|仅示例|示例内容)",
        re.IGNORECASE,
    )
    required_skill_markers = (
        "心智模型",
        "决策启发式",
        "表达DNA",
        "诚实边界",
        "调研来源",
        "时间线",
    )
    missing: list[str] = []
    sparse: list[str] = []
    observed: list[str] = []
    for path in required:
        try:
            if not path.exists():
                missing.append(str(path))
                continue
            observed.append(str(path))
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                stripped = text.strip()
                # Tiny shells are worse than an explicit blocker for reusable skills.
                if path.name == "SKILL.md":
                    if not stripped.startswith("---"):
                        sparse.append(f"{path} (missing_frontmatter)")
                    min_chars = 4000 if requires_huashu_research else 1000
                    if len(stripped) < min_chars:
                        sparse.append(f"{path} (too_short:{len(stripped)}<{min_chars})")
                    if requires_huashu_research:
                        missing_markers = [marker for marker in required_skill_markers if marker not in stripped]
                        if missing_markers:
                            sparse.append(f"{path} (missing_sections:{','.join(missing_markers)})")
                else:
                    min_chars = 500 if requires_huashu_research else 120
                    if len(stripped) < min_chars:
                        sparse.append(f"{path} (too_short:{len(stripped)}<{min_chars})")
                    if requires_huashu_research and not re.search(r"https?://|来源|source|官方|HoYo|米哈游|可信|confidence", stripped, re.IGNORECASE):
                        sparse.append(f"{path} (missing_sources)")
                if placeholder_re.search(stripped):
                    sparse.append(str(path))
        except Exception:
            missing.append(str(path))
    if not missing and not sparse:
        return None
    transcript = _compact_transcript(delta_messages, limit=1200)
    return {
        "status": "failed",
        "error": "artifact_acceptance_failed",
        "dispatchStatus": "artifact_missing_or_sparse",
        "missingArtifacts": missing,
        "sparseArtifacts": sparse,
        "observedArtifacts": observed,
        "localSelfCheck": "Subagent returned before producing required workspace skill artifacts.",
        "acceptanceHint": (
            "Retry after the research handoff is available; write the required SKILL.md and references before reporting success."
        ),
        "compactTranscript": transcript,
    }


def _child_request_from_send_state(
    child_state: dict[str, Any],
    *,
    source_branch: dict[str, Any],
    source_agent_id: str,
    seed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(child_state, dict):
        return None
    child_branch = dict(child_state.get("parallel_branch") or {})
    if not child_branch:
        return None
    seed = dict(seed or {})
    child_invocation_id = str(child_branch.get("invocationId") or seed.get("childInvocationId") or "").strip()
    child_delegation_id = str(child_branch.get("delegationId") or seed.get("childDelegationId") or "").strip()
    request_id = str(seed.get("requestId") or "").strip()
    if not request_id:
        stable_part = child_invocation_id or child_delegation_id
        request_id = f"child_{stable_part}" if stable_part else f"child_{uuid.uuid4().hex[:12]}"
    child_task_brief = (
        dict(seed.get("childTaskBrief"))
        if isinstance(seed.get("childTaskBrief"), dict)
        else dict(child_branch.get("taskBrief"))
        if isinstance(child_branch.get("taskBrief"), dict)
        else {}
    )

    def _first_text(*values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    child_task_brief_id = _first_text(
        seed.get("childTaskBriefId"),
        child_branch.get("taskBriefId"),
        child_task_brief.get("taskBriefId"),
        child_task_brief.get("id"),
    )
    id_like_values = {
        value
        for value in (
            child_task_brief_id,
            child_invocation_id,
            child_delegation_id,
            child_task_brief.get("id"),
        )
        if str(value or "").strip()
    }
    child_task_goal = ""
    for value in (
        seed.get("childTaskGoal"),
        child_task_brief.get("goal"),
        child_task_brief.get("brief"),
        child_task_brief.get("title"),
        child_branch.get("reason"),
    ):
        text = str(value or "").strip()
        if text and text not in id_like_values:
            child_task_goal = text
            break
    return {
        "requestId": request_id,
        "createdAt": seed.get("createdAt") or _now_iso(),
        "sourceInvocationId": seed.get("sourceInvocationId") or source_branch.get("invocationId"),
        "sourceDelegationId": seed.get("sourceDelegationId") or source_branch.get("delegationId"),
        "sourceAgentId": seed.get("sourceAgentId") or source_agent_id,
        "sourceAgentName": seed.get("sourceAgentName") or source_branch.get("agentName") or source_agent_id,
        "sourceAllowChildDelegation": bool(source_branch.get("allowChildDelegation")),
        "sourceChildDelegationBudget": dict(source_branch.get("childDelegationBudget") or {}),
        "childInvocationId": child_invocation_id or seed.get("childInvocationId"),
        "childDelegationId": child_delegation_id or seed.get("childDelegationId"),
        "childTaskBriefId": child_task_brief_id,
        "childTaskGoal": child_task_goal,
        **({"childTaskBrief": child_task_brief} if child_task_brief else {}),
        "childAgentId": seed.get("childAgentId") or child_branch.get("agentId"),
        "childAgentName": seed.get("childAgentName") or child_branch.get("agentName"),
        "childDepth": seed.get("childDepth") or child_branch.get("delegationDepth"),
        "send": {
            "node": "parallel_delegate_task",
            "arg": child_state,
        },
    }


def _child_requests_from_pending_records(
    pending: Any,
    *,
    source_branch: dict[str, Any],
    source_agent_id: str,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for raw in list(pending or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        send_data = item.get("send") if isinstance(item.get("send"), dict) else {}
        node = str(send_data.get("node") or item.get("node") or "").strip()
        arg = send_data.get("arg") if isinstance(send_data.get("arg"), dict) else item.get("arg")
        if not isinstance(arg, dict):
            child_branch = item.get("childBranch") if isinstance(item.get("childBranch"), dict) else {}
            if child_branch:
                arg = {"parallel_branch": dict(child_branch)}
        if node and node != "parallel_delegate_task":
            continue
        request = _child_request_from_send_state(
            arg,
            source_branch=source_branch,
            source_agent_id=source_agent_id,
            seed=item,
        ) if isinstance(arg, dict) else None
        if request:
            requests.append(request)
    return requests


def _dedupe_child_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requests:
        key = str(
            item.get("requestId")
            or item.get("childDelegationId")
            or item.get("childInvocationId")
            or ""
        ).strip()
        if not key:
            key = uuid.uuid4().hex
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _grandchild_verification_brief(
    *,
    parent_task_brief: dict[str, Any],
    child_task_brief_id: str,
    fallback_goal: str,
) -> dict[str, Any]:
    """Preserve the parent's exact verification intent for a disposable mirror.

    Supervisor-authored task briefs often carry an explicit grandchild contract
    in context. Losing that object during Send recovery turns an exact command
    and output check into a vague "verify independently" prompt, which is an
    information failure rather than model disobedience.
    """

    context = parent_task_brief.get("context") if isinstance(parent_task_brief.get("context"), dict) else {}
    explicit = next(
        (
            dict(value)
            for value in (
                context.get("mandatoryGrandchildBrief"),
                context.get("grandchildContract"),
                context.get("childVerificationContract"),
                context.get("childDelegationContract"),
            )
            if isinstance(value, dict)
        ),
        {},
    )
    child = dict(explicit)
    child["taskBriefId"] = child_task_brief_id
    child["goal"] = str(
        child.get("goal")
        or f"Independently verify the final result of the parent task: {fallback_goal}"
    ).strip()
    child["readSet"] = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in [
                *list(child.get("readSet") or []),
                *list(parent_task_brief.get("writeSet") or []),
            ]
            if str(item or "").strip()
        )
    )
    child["writeSet"] = []
    child["readOnly"] = True
    child["writeRequired"] = False
    child["expectedArtifacts"] = []
    parent_expectations = _verification_expectations({"taskBrief": parent_task_brief})
    parent_contract_blob = "\n".join(
        _stringify_for_acceptance(value)
        for value in (
            parent_task_brief.get("acceptanceTiers"),
            parent_task_brief.get("acceptanceContract"),
            parent_task_brief.get("expectedOutputs"),
            parent_task_brief.get("proofExpectations"),
        )
    )
    requires_execution = bool(
        parent_expectations.get("requiredCommands")
        or parent_expectations.get("expectedStdout")
        or re.search(
            r"(?:实际执行|执行退出码|运行结果|stdout|stderr|run the command|execute the|exit code)",
            parent_contract_blob,
            re.IGNORECASE,
        )
    )
    required_commands = list(parent_expectations.get("requiredCommands") or [])
    if requires_execution and not required_commands:
        for path in list(child.get("readSet") or []):
            normalized_path = str(path or "").strip().replace("\\", "/")
            if normalized_path.lower().endswith(".py"):
                required_commands.append(f"python {normalized_path}")
            elif normalized_path.lower().endswith((".js", ".mjs", ".cjs")):
                required_commands.append(f"node {normalized_path}")
    focused_evidence_contract = {
        "requiredReadPaths": list(child.get("readSet") or []),
        "requiredCommands": required_commands,
        "requiredCommandTargets": (
            [] if required_commands else list(child.get("readSet") or []) if requires_execution else []
        ),
        "expectedStdout": list(parent_expectations.get("expectedStdout") or []),
        "expectEmptyStderr": bool(parent_expectations.get("expectEmptyStderr")),
    }
    child_context = child.get("context") if isinstance(child.get("context"), dict) else {}
    child_context["verificationEvidenceContract"] = focused_evidence_contract
    child["context"] = child_context
    if not list(child.get("expectedOutputs") or []):
        child["expectedOutputs"] = [
            "Successful read evidence for every declared verification path",
            *(
                ["Successful command evidence with command, exit code, exact stdout, and stderr"]
                if requires_execution
                else []
            ),
            "Compact independent verification handoff for the parent Agent",
        ]
    if child.get("acceptanceContract") in (None, "", [], {}):
        focused_must = [
            *(f"Read the final file with read_native_file: {path}" for path in child["readSet"]),
            *(f"Execute with run_system_command: {command}" for command in required_commands),
            *(
                ["The verification command must complete with exit code 0."]
                if requires_execution
                else []
            ),
            *(
                f"The command stdout must equal exactly: {expected}"
                for expected in focused_evidence_contract["expectedStdout"]
            ),
            *(
                ["The command stderr must be empty."]
                if focused_evidence_contract["expectEmptyStderr"]
                else []
            ),
            "Return the real ToolMessage evidence to the parent; do not create files or delegate again.",
        ]
        child["acceptanceContract"] = {"must": focused_must}
    tool_policy = child.get("toolPolicy") if isinstance(child.get("toolPolicy"), dict) else {}
    if not tool_policy:
        # The evidence contract says what must be proven; it is not an
        # authorization allowlist. Terminal workers keep the role's public
        # toolbox and are expected to select only the relevant tools.
        tool_policy = {"mode": "default"}
    child["toolPolicy"] = tool_policy
    child["allowedTools"] = list(tool_policy.get("allowedTools") or [])
    child["allowChildDelegation"] = False
    child["requireChildDelegation"] = False
    child["childDelegationPolicyExplicit"] = True
    for key in (
        "targetAgentName",
        "target_agent_name",
        "preferredAgentId",
        "preferred_agent_id",
        "agentId",
        "agentName",
    ):
        child.pop(key, None)

    derived = derive_grandchild_engineering_task(parent_task_brief, child)
    derived_context = derived.get("context") if isinstance(derived.get("context"), dict) else {}
    evidence_contract = _verification_expectations({"taskBrief": derived})
    derived_context["verificationEvidenceContract"] = {
        "requiredReadPaths": evidence_contract["requiredReadPaths"],
        "requiredCommands": evidence_contract["requiredCommands"],
        "requiredCommandTargets": evidence_contract["requiredCommandTargets"],
        "expectedStdout": evidence_contract["expectedStdout"],
        "expectEmptyStderr": evidence_contract["expectEmptyStderr"],
    }
    derived["context"] = derived_context
    return derived


def _render_required_child_contract(branch: dict[str, Any]) -> str:
    parent_task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    child = _grandchild_verification_brief(
        parent_task_brief=parent_task_brief,
        child_task_brief_id=f"{branch.get('taskBriefId') or branch.get('invocationId') or 'task'}:verification",
        fallback_goal=str(branch.get("reason") or parent_task_brief.get("goal") or "assigned task"),
    )
    visible = {
        "taskBriefId": child.get("taskBriefId"),
        "goal": child.get("goal"),
        "readSet": child.get("readSet"),
        "writeSet": [],
        "expectedOutputs": child.get("expectedOutputs"),
        "acceptanceContract": child.get("acceptanceContract"),
        "toolPolicy": child.get("toolPolicy"),
        "allowChildDelegation": False,
        "requireChildDelegation": False,
        "childDelegationPolicyExplicit": True,
        "childDelegationBudget": {},
    }
    return json.dumps(visible, ensure_ascii=False, separators=(",", ":"))[:6000]


def _fallback_child_delegation_request(
    *,
    branch: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Repair an incomplete nested dispatch without inventing new authority.

    Some providers return the delegation ``Send`` boundary without preserving
    the typed pending-child payload.  Both the in-graph and durable runner paths
    must promote the same conservative, read-only mirror instead of turning the
    direct Agent's valid request into a terminal blocker.
    """

    source_invocation_id = str(
        summary.get("invocationId")
        or branch.get("invocationId")
        or uuid.uuid4().hex[:12]
    ).strip()
    source_delegation_id = str(
        summary.get("delegationId") or branch.get("delegationId") or ""
    ).strip()
    child_invocation_id = f"{source_invocation_id}:child:{uuid.uuid4().hex[:8]}"
    child_delegation_id = f"{source_delegation_id or source_invocation_id}:child"
    task_goal = str(
        summary.get("taskGoal")
        or branch.get("reason")
        or "Continue the child delegation requested by the subagent."
    ).strip()
    parent_task_brief = (
        branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    )
    child_task_brief = _grandchild_verification_brief(
        parent_task_brief=parent_task_brief,
        child_task_brief_id=f"{child_invocation_id}:brief",
        fallback_goal=task_goal,
    )
    child_branch = {
        "invocationId": child_invocation_id,
        "delegationId": child_delegation_id,
        "taskBriefId": f"{child_invocation_id}:brief",
        "reason": str(child_task_brief.get("goal") or f"Independently verify: {task_goal}"),
        "delegationDepth": int(branch.get("delegationDepth") or 0) + 1,
        "runtimeAccess": ["delegation.recursive"],
        "allowChildDelegation": False,
        "taskBrief": child_task_brief,
    }
    return {
        "requestId": f"fallback_child_{child_invocation_id}",
        "createdAt": _now_iso(),
        "sourceInvocationId": source_invocation_id,
        "sourceDelegationId": source_delegation_id or None,
        "sourceAgentId": summary.get("agentId") or branch.get("agentId"),
        "sourceAgentName": summary.get("agentName") or branch.get("agentName"),
        "childInvocationId": child_invocation_id,
        "childDelegationId": child_delegation_id,
        "childTaskBriefId": child_branch["taskBriefId"],
        "childTaskGoal": child_branch["reason"],
        "childTaskBrief": child_task_brief,
        "childAgentId": child_branch.get("agentId"),
        "childAgentName": child_branch.get("agentName"),
        "childDepth": child_branch["delegationDepth"],
        "fallbackReason": "incomplete_nested_delegation_payload",
        "send": {
            "node": "parallel_delegate_task",
            "arg": {
                "parallel_branch": child_branch,
                "messages": [],
                "todos": [],
            },
        },
    }


def _extract_child_delegation_requests(
    goto: Any,
    *,
    source_branch: dict[str, Any],
    source_agent_id: str,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    if isinstance(goto, list):
        items = goto
    elif isinstance(goto, (Command, Send)):
        items = [goto]
    elif isinstance(goto, dict):
        items = [goto]
    else:
        items = []
    for item in items:
        if isinstance(item, Command):
            update = getattr(item, "update", None)
            if isinstance(update, dict):
                requests.extend(
                    _child_requests_from_pending_records(
                        update.get("pending_child_delegations"),
                        source_branch=source_branch,
                        source_agent_id=source_agent_id,
                    )
                )
            requests.extend(
                _extract_child_delegation_requests(
                    getattr(item, "goto", None),
                    source_branch=source_branch,
                    source_agent_id=source_agent_id,
                )
            )
            continue
        if isinstance(item, dict):
            requests.extend(
                _child_requests_from_pending_records(
                    item.get("pending_child_delegations")
                    or (item.get("update") or {}).get("pending_child_delegations")
                    if isinstance(item.get("update"), dict)
                    else item.get("pending_child_delegations"),
                    source_branch=source_branch,
                    source_agent_id=source_agent_id,
                )
            )
            if "goto" in item:
                requests.extend(
                    _extract_child_delegation_requests(
                        item.get("goto"),
                        source_branch=source_branch,
                        source_agent_id=source_agent_id,
                    )
                )
            maybe_node = str(item.get("node") or "").strip()
            maybe_arg = item.get("arg")
            if maybe_node == "parallel_delegate_task" and isinstance(maybe_arg, dict):
                request = _child_request_from_send_state(
                    maybe_arg,
                    source_branch=source_branch,
                    source_agent_id=source_agent_id,
                )
                if request:
                    requests.append(request)
            continue
        if not isinstance(item, Send):
            continue
        if str(getattr(item, "node", "") or "") != "parallel_delegate_task":
            continue
        child_state = getattr(item, "arg", None)
        request = _child_request_from_send_state(
            child_state,
            source_branch=source_branch,
            source_agent_id=source_agent_id,
        )
        if request:
            requests.append(request)
    return _dedupe_child_requests(requests)


def _child_delegation_block_reason(branch: dict[str, Any], child_requests: list[dict[str, Any]]) -> str | None:
    if not child_requests:
        return None
    if not bool(branch.get("allowChildDelegation")):
        return "child_delegation_not_allowed"
    budget = branch.get("childDelegationBudget") if isinstance(branch.get("childDelegationBudget"), dict) else {}
    max_depth = budget.get("maxDepth")
    if max_depth is not None:
        try:
            current_depth = int(branch.get("delegationDepth") or 0)
            if current_depth > int(max_depth):
                return "child_delegation_depth_exceeded"
        except Exception:
            pass
    max_children = budget.get("maxChildren")
    if max_children is not None:
        try:
            if len(child_requests) > int(max_children):
                return "child_delegation_children_exceeded"
        except Exception:
            pass
    return None


def _child_delegation_block_summary(
    *,
    branch: dict[str, Any],
    agent_id: str,
    child_requests: list[dict[str, Any]],
    reason: str,
    delta_messages: list[Any],
    delta_todos: list[Any],
    tool_mode: Any,
) -> dict[str, Any]:
    return {
        "invocationId": branch.get("invocationId"),
        "taskBriefId": branch.get("taskBriefId"),
        "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
        "taskGoal": branch.get("reason"),
        "agentId": agent_id,
        "agentName": branch.get("agentName") or agent_id,
        "delegationId": branch.get("delegationId"),
        "lane": branch.get("lane") or "subagent",
        "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
        "targetLabel": branch.get("agentName") or agent_id,
        "branchIndex": branch.get("branchIndex"),
        "status": "blocked",
        "error": reason,
        "dispatchStatus": "dispatch_missing_child_budget" if reason == "child_delegation_not_allowed" else reason,
        "blockedChildDelegationCount": len(child_requests),
        "childDelegationCount": 0,
        "childDelegationRequestIds": [],
        "completedAt": _now_iso(),
        "messageCount": len(delta_messages),
        "todoDeltaCount": len(delta_todos),
        "toolMode": tool_mode,
        "toolsUsed": _extract_tool_names(delta_messages),
        "compactTranscript": _compact_transcript(delta_messages),
        "localSelfCheck": (
            "Subagent requested child delegation, but this branch did not have explicit child delegation budget. "
            "The nested dispatch was blocked to avoid recursive branch explosion."
        ),
        "acceptanceHint": "Route a new delegation episode with explicit allowChildDelegation and childDelegationBudget if child work is still required.",
    }


async def _run_parallel_agent_branch(
    state: dict[str, Any],
    agent_data: dict[str, Any],
    *,
    progress_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[list[Any], list[Any], dict[str, Any], list[dict[str, Any]]]:
    branch = dict(state.get("parallel_branch") or {})
    agent_id = str(branch.get("agentId") or "")
    current_node = agent_id
    local_state = dict(state)
    local_state["messages"] = list(state.get("messages") or [])
    local_state["todos"] = list(state.get("todos") or [])
    initial_message_count = int(branch.get("initialMessageCount") or len(local_state["messages"]))
    initial_todo_count = int(branch.get("initialTodoCount") or len(local_state["todos"]))

    repeated_state_limit = 8
    seen_progress_states: dict[str, int] = {}
    repeat_sensitive_tool_limit = 2
    repeat_tool_correction_used = False
    required_child_correction_count = 0
    verification_correction_count = 0
    verification_command_correction_count = 0
    seen_verification_command_call_ids: set[str] = set()
    creative_evidence_correction_count = 0
    artifact_correction_count = 0
    seen_tool_call_ids: set[str] = set()
    repeated_tool_signatures: dict[tuple[str, str], int] = {}
    verification_expectations = _verification_expectations(branch)
    required_verification_commands = list(verification_expectations.get("requiredCommands") or [])
    expected_artifact_paths = _infer_expected_artifact_paths(branch, local_state)
    initial_artifact_snapshot = _artifact_progress_snapshot(expected_artifact_paths)
    required_child_delegation = task_brief_requires_child_delegation(
        branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None
    )
    artifact_snapshot = initial_artifact_snapshot
    artifact_stall_rounds = 0
    artifact_stall_limit = 80
    last_progress_node = ""
    model_turn_index = 0
    _publish_parallel_progress(
        progress_callback,
        stage="started",
        status="running",
        summary=f"{branch.get('agentName') or agent_id} 已开始处理任务。",
    )
    while True:
        if current_node != last_progress_node:
            stage = "working"
            summary = f"{branch.get('agentName') or agent_id} 正在处理任务。"
            if current_node.endswith("_tools"):
                stage = "tool_execution"
                summary = f"{branch.get('agentName') or agent_id} 正在执行工具步骤。"
            elif current_node.endswith("_reviewer"):
                stage = "reviewing"
                summary = f"{branch.get('agentName') or agent_id} 正在自检结果。"
            _publish_parallel_progress(progress_callback, stage=stage, status="running", summary=summary)
            last_progress_node = current_node
        progress_fingerprint = _parallel_branch_progress_fingerprint(
            current_node=current_node,
            state=local_state,
            initial_message_count=initial_message_count,
            initial_todo_count=initial_todo_count,
        )
        seen_progress_states[progress_fingerprint] = seen_progress_states.get(progress_fingerprint, 0) + 1
        if seen_progress_states[progress_fingerprint] > repeated_state_limit:
            raise _parallel_branch_error(
                f"{agent_id} 并发分支连续重复同一执行状态，已停止无进展循环。",
                state=local_state,
                initial_message_count=initial_message_count,
            )
        if current_node == agent_id:
            runtime_context = _runtime_context_from_parallel_state(local_state, branch=branch)
            model_turn_index += 1
            runtime_context.update(
                {
                    "subagent_stream_progress_callback": progress_callback,
                    "subagent_model_turn": model_turn_index,
                    "subagent_display_name": branch.get("agentName") or agent_id,
                }
            )

            def _invoke_agent_node() -> Any:
                with bind_runtime_context(**runtime_context):
                    return agent_data["node_func"](local_state)

            result = await asyncio.to_thread(_invoke_agent_node)
        elif current_node == f"{agent_id}_tools":
            tool_node = agent_data.get("tool_node_func")
            if tool_node is None:
                raise _parallel_branch_error(
                    f"{agent_id} 没有可用的工具节点。",
                    state=local_state,
                    initial_message_count=initial_message_count,
                )
            with bind_runtime_context(**_runtime_context_from_parallel_state(local_state, branch=branch)):
                result = await tool_node(
                    local_state,
                    config=build_runtime_callback_config(),
                )
        elif current_node == f"{agent_id}_reviewer":
            reviewer = agent_data.get("reviewer_func")
            if reviewer is None:
                raise RuntimeError(f"{agent_id} 没有可用的 reviewer 节点。")
            runtime_context = _runtime_context_from_parallel_state(local_state, branch=branch)
            model_turn_index += 1
            runtime_context.update(
                {
                    "subagent_stream_progress_callback": progress_callback,
                    "subagent_model_turn": model_turn_index,
                    "subagent_display_name": f"{branch.get('agentName') or agent_id} Reviewer",
                }
            )

            def _invoke_reviewer_node() -> Any:
                with bind_runtime_context(**runtime_context):
                    return reviewer(local_state)

            result = await asyncio.to_thread(_invoke_reviewer_node)
        else:
            raise _parallel_branch_error(
                f"{agent_id} 进入了未识别的并发分支节点：{current_node}",
                state=local_state,
                initial_message_count=initial_message_count,
            )

        if isinstance(result, list):
            delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
            delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
            child_requests = _extract_child_delegation_requests(
                result,
                source_branch=branch,
                source_agent_id=agent_id,
            )
            nested_count = len([item for item in result if isinstance(item, (Command, Send))])
            if not child_requests and nested_count and bool(branch.get("allowChildDelegation")):
                fallback_summary = {
                    "invocationId": branch.get("invocationId"),
                    "delegationId": branch.get("delegationId"),
                    "taskGoal": branch.get("reason"),
                    "agentId": agent_id,
                    "agentName": branch.get("agentName") or agent_id,
                }
                child_requests = [
                    _fallback_child_delegation_request(
                        branch=branch,
                        summary=fallback_summary,
                    )
                ]
            if child_requests:
                _publish_parallel_progress(
                    progress_callback,
                    stage="child_requested",
                    status="waiting",
                    summary=f"{branch.get('agentName') or agent_id} 请求了 {len(child_requests)} 个子任务。",
                )
            block_reason = _child_delegation_block_reason(branch, child_requests)
            if block_reason:
                return delta_messages, delta_todos, _child_delegation_block_summary(
                    branch=branch,
                    agent_id=agent_id,
                    child_requests=child_requests,
                    reason=block_reason,
                    delta_messages=delta_messages,
                    delta_todos=delta_todos,
                    tool_mode=agent_data.get("tool_mode"),
                ), []
            return delta_messages, delta_todos, {
                "invocationId": branch.get("invocationId"),
                "taskBriefId": branch.get("taskBriefId"),
                "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                "taskGoal": branch.get("reason"),
                "agentId": agent_id,
                "agentName": branch.get("agentName") or agent_id,
                "delegationId": branch.get("delegationId"),
                "lane": branch.get("lane") or "subagent",
                "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                "targetLabel": branch.get("agentName") or agent_id,
                "branchIndex": branch.get("branchIndex"),
                "status": "waiting_child_delegation" if child_requests else "blocked",
                "error": "delegation_child_requested",
                "nestedDispatchCount": nested_count,
                "childDelegationRequestIds": [item.get("requestId") for item in child_requests],
                "childDelegationCount": len(child_requests),
                "completedAt": _now_iso(),
                "messageCount": len(delta_messages),
                "todoDeltaCount": len(delta_todos),
                "toolMode": agent_data.get("tool_mode"),
                "toolsUsed": _extract_tool_names(delta_messages),
                "compactTranscript": _compact_transcript(delta_messages),
                "localSelfCheck": "Subagent requested child delegation. The top-level router must schedule it as a child Runtime episode instead of running nested Send inside this branch.",
                "acceptanceHint": "Route the child delegation through runtime_broker/delegation_broker with explicit child budget; do not assume the child work completed.",
            }, child_requests
        if not isinstance(result, Command):
            raise _parallel_branch_error(
                f"{agent_id} 并发分支返回了非 Command 结果。",
                state=local_state,
                initial_message_count=initial_message_count,
            )

        result_update = getattr(result, "update", None) or {}
        update_messages = list(result_update.get("messages") or []) if isinstance(result_update, dict) else []
        preflight_command_deviations = (
            _verification_command_preflight_deviations(
                update_messages,
                required_verification_commands,
            )
            if current_node == agent_id
            else []
        )
        if preflight_command_deviations:
            verification_command_correction_count += 1
            exact_commands = json.dumps(required_verification_commands, ensure_ascii=False, separators=(",", ":"))
            rejected_messages: list[Any] = []
            pending_calls = [
                call
                for message in update_messages
                for call in _tool_call_dicts_from_message(message)
                if str(call.get("id") or "").strip()
            ]
            for pending_call in pending_calls:
                tool_call_id = str(pending_call.get("id") or "").strip()
                tool_name = str(pending_call.get("name") or "tool").strip() or "tool"
                seen_verification_command_call_ids.add(tool_call_id)
                rejected_messages.append(
                    ToolMessage(
                        content=(
                            "V8OS did not execute this tool call because the batch contained a shell command that "
                            "deviated from the exact verification contract. Reissue only the exact bounded command."
                        ),
                        name=tool_name,
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                )
            rejected_messages.append(
                HumanMessage(
                    content=(
                        "[V8OS exact verification command preflight correction]\n"
                        "The latest tool batch was rejected before execution; no command or sibling tool in that batch ran. "
                        f"Your next action must be exactly one `run_system_command` call whose `command` value equals one of: {exact_commands}. "
                        "Do not add shell variables, redirection, pipes, wrappers, probes, or alternate runners. "
                        "Use mode='sync', timeout_seconds<=90, and preserve cwd separately."
                    ),
                    additional_kwargs={
                        "v8_governance_type": "verification_command_preflight_correction",
                        "v8_correction_attempt": verification_command_correction_count,
                    },
                )
            )
            result_update = {
                **dict(result_update),
                "messages": [*update_messages, *rejected_messages],
            }
            update_messages = list(result_update["messages"])
        exact_command_adjustments = (
            _normalize_exact_verification_command_invocations(
                update_messages,
                required_verification_commands,
            )
            if isinstance(result_update, dict) and current_node == agent_id and not preflight_command_deviations
            else []
        )
        local_state = _merge_state_update(local_state, result_update)
        if exact_command_adjustments:
            _publish_parallel_progress(
                progress_callback,
                stage="execution_normalized",
                status="running",
                summary=f"{branch.get('agentName') or agent_id} 的精确短验证已在执行前规范为同步命令。",
                commandCount=len(exact_command_adjustments),
            )
        for message in list(result_update.get("messages") or []) if isinstance(result_update, dict) else []:
            for timeline_node in _subagent_timeline_nodes_from_message(message):
                topic = str(timeline_node.get("topic") or "").strip()
                tool_name = str(timeline_node.get("toolName") or "").strip()
                stage = (
                    "tool_started"
                    if topic == "subagent.tool.started"
                    else "tool_finished"
                    if topic == "subagent.tool.finished"
                    else "reasoning"
                    if topic == "subagent.reasoning.delta"
                    else "responding"
                )
                summary = (
                    f"正在使用 {tool_name}。"
                    if stage == "tool_started"
                    else f"{tool_name} 已返回结果。"
                    if stage == "tool_finished"
                    else f"{branch.get('agentName') or agent_id} 正在核对证据。"
                    if stage == "reasoning"
                    else f"{branch.get('agentName') or agent_id} 正在回传进展。"
                )
                _publish_parallel_progress(
                    progress_callback,
                    stage=stage,
                    status="running",
                    summary=summary,
                    toolName=tool_name or None,
                    timelineNode=timeline_node,
                )
        if preflight_command_deviations:
            delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
            delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
            if verification_command_correction_count > 2:
                return delta_messages, delta_todos, {
                    "invocationId": branch.get("invocationId"),
                    "taskBriefId": branch.get("taskBriefId"),
                    "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                    "taskGoal": branch.get("reason"),
                    "agentId": agent_id,
                    "agentName": branch.get("agentName") or agent_id,
                    "delegationId": branch.get("delegationId"),
                    "lane": branch.get("lane") or "subagent",
                    "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                    "targetLabel": branch.get("agentName") or agent_id,
                    "branchIndex": branch.get("branchIndex"),
                    "status": "failed",
                    "error": "verification_command_not_exact",
                    "requiredCommands": required_verification_commands,
                    "attemptedCommand": str(preflight_command_deviations[0].get("command") or "")[:1000],
                    "completedAt": _now_iso(),
                    "messageCount": len(delta_messages),
                    "todoDeltaCount": len(delta_todos),
                    "toolMode": agent_data.get("tool_mode"),
                    "toolsUsed": _extract_tool_names(delta_messages),
                    "compactTranscript": _compact_transcript(delta_messages),
                    "localSelfCheck": "The worker ignored two pre-execution exact-command corrections; no deviating command was executed.",
                    "acceptanceHint": "Retry only with the exact declared verification command; do not widen shell policy.",
                }, []
            _publish_parallel_progress(
                progress_callback,
                stage="discipline_corrected",
                status="running",
                summary=(
                    f"{branch.get('agentName') or agent_id} 的非精确验证命令已在执行前拒绝"
                    f"（{verification_command_correction_count}/2）。"
                ),
            )
            current_node = agent_id
            continue
        delta_messages_for_guard = list(local_state.get("messages") or [])[initial_message_count:]
        child_requests = _extract_child_delegation_requests(
            result,
            source_branch=branch,
            source_agent_id=agent_id,
        )
        if child_requests:
            delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
            _publish_parallel_progress(
                progress_callback,
                stage="child_requested",
                status="waiting",
                summary=f"{branch.get('agentName') or agent_id} 请求了 {len(child_requests)} 个子任务。",
            )
            block_reason = _child_delegation_block_reason(branch, child_requests)
            if block_reason:
                return delta_messages_for_guard, delta_todos, _child_delegation_block_summary(
                    branch=branch,
                    agent_id=agent_id,
                    child_requests=child_requests,
                    reason=block_reason,
                    delta_messages=delta_messages_for_guard,
                    delta_todos=delta_todos,
                    tool_mode=agent_data.get("tool_mode"),
                ), []
            return delta_messages_for_guard, delta_todos, {
                "invocationId": branch.get("invocationId"),
                "taskBriefId": branch.get("taskBriefId"),
                "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                "taskGoal": branch.get("reason"),
                "agentId": agent_id,
                "agentName": branch.get("agentName") or agent_id,
                "delegationId": branch.get("delegationId"),
                "lane": branch.get("lane") or "subagent",
                "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                "targetLabel": branch.get("agentName") or agent_id,
                "branchIndex": branch.get("branchIndex"),
                "status": "waiting_child_delegation",
                "error": "delegation_child_requested",
                "childDelegationRequestIds": [item.get("requestId") for item in child_requests],
                "childDelegationCount": len(child_requests),
                "completedAt": _now_iso(),
                "messageCount": len(delta_messages_for_guard),
                "todoDeltaCount": len(delta_todos),
                "toolMode": agent_data.get("tool_mode"),
                "toolsUsed": _extract_tool_names(delta_messages_for_guard),
                "compactTranscript": _compact_transcript(delta_messages_for_guard),
                "localSelfCheck": "Subagent requested child delegation through delegation_broker. The durable router must schedule the returned Send instead of swallowing the Command goto.",
                "acceptanceHint": "Wait for the brokered child delegation result before merging or judging this branch.",
            }, child_requests
        new_command_records = [
            record
            for record in _tool_execution_records(delta_messages_for_guard)
            if record.get("tool") == "run_system_command"
            and str(record.get("toolCallId") or "").strip()
            and str(record.get("toolCallId") or "").strip() not in seen_verification_command_call_ids
        ]
        for record in new_command_records:
            seen_verification_command_call_ids.add(str(record.get("toolCallId") or "").strip())
        command_deviation = next(
            (
                record
                for record in new_command_records
                if required_verification_commands
                and not _verification_command_matches_exact(
                    record.get("command"),
                    required_verification_commands,
                )
            ),
            None,
        )
        if command_deviation:
            verification_command_correction_count += 1
            if verification_command_correction_count > 2:
                delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
                return delta_messages_for_guard, delta_todos, {
                    "invocationId": branch.get("invocationId"),
                    "taskBriefId": branch.get("taskBriefId"),
                    "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                    "taskGoal": branch.get("reason"),
                    "agentId": agent_id,
                    "agentName": branch.get("agentName") or agent_id,
                    "delegationId": branch.get("delegationId"),
                    "lane": branch.get("lane") or "subagent",
                    "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                    "targetLabel": branch.get("agentName") or agent_id,
                    "branchIndex": branch.get("branchIndex"),
                    "status": "failed",
                    "error": "verification_command_not_exact",
                    "requiredCommands": required_verification_commands,
                    "attemptedCommand": str(command_deviation.get("command") or "").strip()[:1000],
                    "completedAt": _now_iso(),
                    "messageCount": len(delta_messages_for_guard),
                    "todoDeltaCount": len(delta_todos),
                    "toolMode": agent_data.get("tool_mode"),
                    "toolsUsed": _extract_tool_names(delta_messages_for_guard),
                    "compactTranscript": _compact_transcript(delta_messages_for_guard),
                    "localSelfCheck": (
                        "The verification worker ignored two exact-command corrections and continued using wrappers, "
                        "redirection, probes, or alternate commands. Those calls are not acceptance evidence."
                    ),
                    "acceptanceHint": "Retry only with the exact declared verification command; do not widen shell policy.",
                }, []
            exact_commands = json.dumps(required_verification_commands, ensure_ascii=False, separators=(",", ":"))
            local_state = _merge_state_update(
                local_state,
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                "[V8OS exact verification command correction]\n"
                                f"The last command was not executed as acceptance evidence because it deviated from the declared command: "
                                f"{str(command_deviation.get('command') or '').strip()[:800]}. "
                                f"Your next action must be exactly one `run_system_command` call whose `command` value equals one of: {exact_commands}. "
                                "Do not add shell variables, redirection, pipes, cmd/powershell wrappers, path discovery, version probes, "
                                "temporary files, or alternate runners. Preserve cwd separately in the tool argument. For a bounded test "
                                "command use mode='sync' with timeout_seconds no greater than 90 so the ToolMessage contains the terminal exit code."
                            ),
                            additional_kwargs={
                                "v8_governance_type": "verification_command_exactness_correction",
                                "v8_correction_attempt": verification_command_correction_count,
                            },
                        )
                    ]
                },
            )
            _publish_parallel_progress(
                progress_callback,
                stage="discipline_corrected",
                status="running",
                summary=(
                    f"{branch.get('agentName') or agent_id} 正在改用验收合同声明的精确验证命令"
                    f"（{verification_command_correction_count}/2）。"
                ),
            )
            current_node = agent_id
            continue
        repeated_tool_violation: tuple[str, str] | None = None
        for message in delta_messages_for_guard:
            for call in _tool_call_dicts_from_message(message):
                signature = _repeat_sensitive_tool_call_signature(call)
                if not signature:
                    continue
                call_id = str(call.get("id") or "").strip() or f"{signature[0]}:{signature[1]}:{len(seen_tool_call_ids)}"
                if call_id in seen_tool_call_ids:
                    continue
                seen_tool_call_ids.add(call_id)
                repeated_tool_signatures[signature] = repeated_tool_signatures.get(signature, 0) + 1
                if repeated_tool_signatures[signature] > repeat_sensitive_tool_limit:
                    repeated_tool_violation = signature
                    break
            if repeated_tool_violation:
                break
        if repeated_tool_violation:
            if repeat_tool_correction_used:
                raise _parallel_branch_error(
                    f"{agent_id} repeated the same tool purpose too many times after a discipline correction: "
                    f"{repeated_tool_violation[0]} {repeated_tool_violation[1][:180]}.",
                    state=local_state,
                    initial_message_count=initial_message_count,
                )
            pending_calls = [
                call
                for message in list(result_update.get("messages") or [])
                for call in _tool_call_dicts_from_message(message)
            ]
            if not pending_calls:
                raise _parallel_branch_error(
                    f"{agent_id} repeated the same tool purpose too many times: "
                    f"{repeated_tool_violation[0]} {repeated_tool_violation[1][:180]}.",
                    state=local_state,
                    initial_message_count=initial_message_count,
                )
            repeat_tool_correction_used = True
            correction_messages: list[Any] = []
            for pending_call in pending_calls:
                tool_name = str(pending_call.get("name") or "tool").strip() or "tool"
                tool_call_id = str(pending_call.get("id") or "").strip()
                if not tool_call_id:
                    continue
                correction_messages.append(
                    ToolMessage(
                        content=(
                            "V8OS did not execute this call because the same evidence purpose has already "
                            "been satisfied by earlier ToolMessages. Reuse the existing result and do not "
                            "retry this tool purpose."
                        ),
                        name=tool_name,
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                )
            next_action = (
                "If independent child verification is still required, your next action is one complete "
                "delegation_broker dispatch; otherwise return the final typed handoff now."
                if bool(branch.get("allowChildDelegation"))
                else "Return the final typed evidence handoff now."
            )
            correction_messages.append(
                HumanMessage(
                    content=(
                        "[V8OS delegated execution discipline correction]\n"
                        "The latest repeated tool calls were intentionally not executed. The required read or "
                        "command evidence is already present in prior ToolMessages. Stop alternate encodings, "
                        "extra probes, skill lookup, and unrelated network calls. Review the acceptance contract. "
                        f"{next_action} If a required fact is genuinely absent, return one concrete blocker instead."
                    ),
                    additional_kwargs={"v8_governance_type": "delegated_execution_correction"},
                )
            )
            local_state = _merge_state_update(local_state, {"messages": correction_messages})
            _publish_parallel_progress(
                progress_callback,
                stage="discipline_corrected",
                status="running",
                summary=f"{branch.get('agentName') or agent_id} 已停止重复工具调用并进入结果收敛。",
            )
            current_node = agent_id
            continue
        if expected_artifact_paths:
            next_snapshot = _artifact_progress_snapshot(expected_artifact_paths)
            missing_artifacts = [path for path in expected_artifact_paths if not path.exists()]
            if not missing_artifacts:
                artifact_snapshot = next_snapshot
                artifact_stall_rounds = 0
            elif next_snapshot != artifact_snapshot:
                artifact_snapshot = next_snapshot
                artifact_stall_rounds = 0
            else:
                artifact_stall_rounds += 1
                if artifact_stall_rounds > artifact_stall_limit:
                    raise _parallel_branch_error(
                        f"{agent_id} 并发分支声明了产物但长期没有文件进展，已停止语义无进展循环。"
                        f" missingArtifacts={[str(path) for path in missing_artifacts[:6]]}",
                        state=local_state,
                        initial_message_count=initial_message_count,
                    )
        goto = getattr(result, "goto", None)
        if isinstance(goto, str):
            if goto == "supervisor":
                control_messages = list(local_state.get("messages") or [])[initial_message_count:]
                continuation_request = _subagent_runtime_input_request(
                    control_messages,
                    branch=branch,
                    agent_id=agent_id,
                )
                if continuation_request:
                    break
                terminal_failure = (
                    _subagent_governance_terminal_failure(control_messages)
                    or _subagent_reported_terminal_failure(
                        _subagent_result_text(control_messages)
                    )
                )
                if terminal_failure:
                    break
                if _is_creative_runtime_execution_branch(branch):
                    creative_evidence = _creative_tool_evidence(control_messages, branch=branch)
                    missing_creative_evidence = list(creative_evidence.get("missingEvidence") or [])
                    if missing_creative_evidence and creative_evidence_correction_count < 2:
                        creative_evidence_correction_count += 1
                        final_retry = creative_evidence_correction_count == 2
                        missing_text = ", ".join(missing_creative_evidence)
                        local_state = _merge_state_update(
                            local_state,
                            {
                                "messages": [
                                    HumanMessage(
                                        content=(
                                            "[V8OS Creative delivery evidence correction]\n"
                                            "This Creative runtime branch has not delivered its governed evidence contract. "
                                            f"Missing: {missing_text}. "
                                            + (
                                                "This is the second and final correction. Call only the real missing Creative facade actions now; "
                                                "if the provider or QA cannot complete, return a typed blocker or request_input instead of prose success. "
                                                if final_retry
                                                else "Continue in this same branch: obtain deliverable refs from the real Creative facade ToolMessages, then run the relevant quality action. "
                                            )
                                            + "A plan, provider job id, final-answer JSON, or prose claim is not artifact/proof evidence. "
                                            "Do not start a replacement runtime and do not repeat completed planning calls."
                                        ),
                                        additional_kwargs={
                                            "v8_governance_type": "creative_delivery_evidence_correction",
                                            "v8_correction_attempt": creative_evidence_correction_count,
                                            "v8_missing_evidence": missing_creative_evidence,
                                        },
                                    )
                                ]
                            },
                        )
                        _publish_parallel_progress(
                            progress_callback,
                            stage="discipline_corrected",
                            status="running",
                            summary=(
                                f"{branch.get('agentName') or agent_id} 正在补齐多媒体交付证据"
                                f"（{creative_evidence_correction_count}/2）。"
                            ),
                        )
                        current_node = agent_id
                        continue
                if required_child_delegation and required_child_correction_count < 2:
                    required_child_correction_count += 1
                    required_child_contract = _render_required_child_contract(branch)
                    if required_child_correction_count == 1:
                        correction_text = (
                            "The must-level acceptance contract requires one grandchild verification. "
                            "Follow this exact order: (1) complete your own assigned write, (2) run your own "
                            "local self-check, (3) call `delegation_broker(mode='dispatch')` exactly once with "
                            "the focused read-only task below, and (4) wait for the child handoff before returning "
                            "your final result. Preserve the task's paths, tools, expected outputs, and acceptance "
                            "criteria. The broker creates a disposable mirror of you; any registered Agent name in "
                            "the surrounding prose is context only, so do not select another registered Agent.\n"
                        )
                    else:
                        correction_text = (
                            "You returned again without satisfying the required child-verification step. The "
                            "transcript already contains the earlier ordered instruction and your work so far. "
                            "Complete only any genuinely missing local self-check, then your next model action must "
                            "be one real `delegation_broker(mode='dispatch')` call with the exact task below. Do not "
                            "return a final result, narrate the call, choose a registered Agent, or repeat completed "
                            "file work.\n"
                        )
                    local_state = _merge_state_update(
                        local_state,
                        {
                            "messages": [
                                HumanMessage(
                                    content=(
                                        "[V8OS delegated acceptance correction]\n"
                                        f"{correction_text}"
                                        f"Required child task: {required_child_contract}\n"
                                        "Do not return a final handoff or describe a tool call in prose."
                                    ),
                                    additional_kwargs={
                                        "v8_governance_type": "required_child_delegation_correction"
                                    },
                                )
                            ]
                        },
                    )
                    _publish_parallel_progress(
                        progress_callback,
                        stage="discipline_corrected",
                        status="running",
                        summary=(
                            f"{branch.get('agentName') or agent_id} 正在补齐验收合同要求的独立子级验证。"
                        ),
                    )
                    current_node = agent_id
                    continue
                if required_child_delegation:
                    delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
                    delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
                    return delta_messages, delta_todos, {
                        "invocationId": branch.get("invocationId"),
                        "taskBriefId": branch.get("taskBriefId"),
                        "taskBrief": branch.get("taskBrief")
                        if isinstance(branch.get("taskBrief"), dict)
                        else None,
                        "taskGoal": branch.get("reason"),
                        "agentId": agent_id,
                        "agentName": branch.get("agentName") or agent_id,
                        "delegationId": branch.get("delegationId"),
                        "lane": branch.get("lane") or "subagent",
                        "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                        "targetLabel": branch.get("agentName") or agent_id,
                        "branchIndex": branch.get("branchIndex"),
                        "status": "blocked",
                        "error": "required_child_delegation_missing",
                        "completedAt": _now_iso(),
                        "messageCount": len(delta_messages),
                        "todoDeltaCount": len(delta_todos),
                        "toolMode": agent_data.get("tool_mode"),
                        "toolsUsed": _extract_tool_names(delta_messages),
                        "compactTranscript": _compact_transcript(delta_messages),
                        "localSelfCheck": (
                            "The worker returned after both the ordered and focused corrections without the "
                            "grandchild verification required by the must-level acceptance contract."
                        ),
                        "acceptanceHint": (
                            "Retry the direct worker with delegation_broker available; do not accept this result."
                        ),
                    }, []
                artifact_status = _required_artifact_write_status(
                    branch=branch,
                    state=local_state,
                    delta_messages=control_messages,
                    agent_data=agent_data,
                )
                write_evidence_missing = bool(
                    artifact_status and artifact_status.get("missingRequiredTool")
                )
                verification_failure = (
                    None
                    if write_evidence_missing
                    else _validate_required_verification_evidence(
                        branch=branch,
                        delta_messages=list(local_state.get("messages") or [])[initial_message_count:],
                    )
                )
                if verification_failure and verification_correction_count < 2:
                    verification_correction_count += 1
                    missing_tools = [
                        str(item).strip()
                        for item in list(verification_failure.get("missingVerificationTools") or [])
                        if str(item).strip()
                    ]
                    required_steps: list[str] = []
                    if "read_native_file" in missing_tools:
                        required_steps.append(
                            "Call `read_native_file` for the declared readSet path and use its successful ToolMessage as evidence."
                        )
                    if "run_system_command" in missing_tools:
                        required_steps.append(
                            "Call `run_system_command` once with the exact verification command from the acceptance contract, "
                            "using the current Active Workspace Root as cwd and mode='sync' with timeout_seconds <= 90; "
                            "require returnCode=0 and preserve stdout/stderr."
                        )
                    mismatches = [
                        str(item).strip()
                        for item in list(verification_failure.get("verificationEvidenceMismatches") or [])
                        if str(item).strip()
                    ]
                    expectations = _verification_expectations(branch)
                    focused_retry = (
                        "This is the final focused correction. Your next action must be the missing real tool call; "
                        "do not emit its arguments as JSON or prose. "
                        if verification_correction_count == 2
                        else ""
                    )
                    local_state = _merge_state_update(
                        local_state,
                        {
                            "messages": [
                                HumanMessage(
                                    content=(
                                        "[V8OS delegated verification correction]\n"
                                        "Your final answer is missing successful tool evidence required by the acceptance contract. "
                                        + focused_retry
                                        + f"Missing tools: {', '.join(missing_tools)}. "
                                        + f"Evidence mismatches: {', '.join(mismatches) or 'none'}. "
                                        + " ".join(required_steps)
                                        + " Exact verification contract: "
                                        + json.dumps(expectations, ensure_ascii=False, separators=(",", ":"))[:5000]
                                        + " Do not call skill lookup, alternate tools, or describe a tool call in prose. "
                                        "After the successful ToolMessages are present, return the compact verification result."
                                    ),
                                    additional_kwargs={
                                        "v8_governance_type": "required_verification_evidence_correction"
                                    },
                                )
                            ]
                        },
                    )
                    _publish_parallel_progress(
                        progress_callback,
                        stage="discipline_corrected",
                        status="running",
                        summary=(
                            f"{branch.get('agentName') or agent_id} 正在补齐验收合同要求的真实验证证据。"
                        ),
                    )
                    current_node = agent_id
                    continue
                missing_artifacts = list((artifact_status or {}).get("missingExpectedArtifacts") or [])
                missing_write_tool = bool((artifact_status or {}).get("missingRequiredTool"))
                if artifact_status and (missing_artifacts or missing_write_tool):
                    if (
                        bool(artifact_status.get("requiredToolVisible"))
                        and artifact_correction_count < 2
                    ):
                        artifact_correction_count += 1
                        final_retry = artifact_correction_count == 2
                        correction_paths = missing_artifacts or list(artifact_status.get("expectedArtifacts") or [])
                        exact_paths = ", ".join(f"`{path}`" for path in correction_paths[:16])
                        local_state = _merge_state_update(
                            local_state,
                            {
                                "messages": [
                                    HumanMessage(
                                        content=(
                                            "[V8OS delegated artifact correction]\n"
                                            "The delegated task is write-required, but successful write evidence is still missing. "
                                            f"You MUST now call the real `write_native_file` tool for these exact workspace paths: {exact_paths}. "
                                            + (
                                                "This is the second and final correction; make the real write call now or return a typed blocker. "
                                                if final_retry
                                                else "Do not put HTML/source content or tool arguments in prose."
                                            )
                                            + " A successful ToolMessage and the resulting file are required before a final handoff."
                                        ),
                                        additional_kwargs={
                                            "v8_governance_type": "required_artifact_tool_correction",
                                            "v8_correction_attempt": artifact_correction_count,
                                            "v8_required_tool": "write_native_file",
                                            "v8_missing_expected_artifacts": missing_artifacts[:16],
                                        },
                                    )
                                ]
                            },
                        )
                        _publish_parallel_progress(
                            progress_callback,
                            stage="discipline_corrected",
                            status="running",
                            summary=(
                                f"{branch.get('agentName') or agent_id} 正在补齐声明的文件产物"
                                f"（{artifact_correction_count}/2）。"
                            ),
                        )
                        current_node = agent_id
                        continue
                break
            current_node = goto
            continue
        if isinstance(goto, list):
            delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
            delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
            child_requests = _extract_child_delegation_requests(
                goto,
                source_branch=branch,
                source_agent_id=agent_id,
            )
            if not child_requests and goto and bool(branch.get("allowChildDelegation")):
                child_requests = [
                    _fallback_child_delegation_request(
                        branch=branch,
                        summary={
                            "invocationId": branch.get("invocationId"),
                            "delegationId": branch.get("delegationId"),
                            "taskGoal": branch.get("reason"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                        },
                    )
                ]
            if child_requests:
                _publish_parallel_progress(
                    progress_callback,
                    stage="child_requested",
                    status="waiting",
                    summary=f"{branch.get('agentName') or agent_id} 请求了 {len(child_requests)} 个子任务。",
                )
                block_reason = _child_delegation_block_reason(branch, child_requests)
                if block_reason:
                    return delta_messages, delta_todos, _child_delegation_block_summary(
                        branch=branch,
                        agent_id=agent_id,
                        child_requests=child_requests,
                        reason=block_reason,
                        delta_messages=delta_messages,
                        delta_todos=delta_todos,
                        tool_mode=agent_data.get("tool_mode"),
                    ), []
                return delta_messages, delta_todos, {
                    "invocationId": branch.get("invocationId"),
                    "taskBriefId": branch.get("taskBriefId"),
                    "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                    "taskGoal": branch.get("reason"),
                    "agentId": agent_id,
                    "agentName": branch.get("agentName") or agent_id,
                    "delegationId": branch.get("delegationId"),
                    "lane": branch.get("lane") or "subagent",
                    "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                    "targetLabel": branch.get("agentName") or agent_id,
                    "branchIndex": branch.get("branchIndex"),
                    "status": "waiting_child_delegation",
                    "error": "delegation_child_requested",
                    "nestedDispatchCount": len(child_requests),
                    "childDelegationRequestIds": [item.get("requestId") for item in child_requests],
                    "childDelegationCount": len(child_requests),
                    "completedAt": _now_iso(),
                    "messageCount": len(delta_messages),
                    "todoDeltaCount": len(delta_todos),
                    "toolMode": agent_data.get("tool_mode"),
                    "toolsUsed": _extract_tool_names(delta_messages),
                    "compactTranscript": _compact_transcript(delta_messages),
                    "localSelfCheck": "Subagent requested child delegation. The top-level router will schedule it as a child Runtime episode instead of running nested Send inside this branch.",
                    "acceptanceHint": "Wait for the child delegation completion event before merging or judging this branch.",
                }, child_requests
        raise _parallel_branch_error(
            f"{agent_id} 并发分支返回了不支持的 goto 类型。",
            state=local_state,
            initial_message_count=initial_message_count,
        )

    delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
    delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
    result_text = _subagent_result_text(delta_messages)
    continuation_request = _subagent_runtime_input_request(
        delta_messages,
        branch=branch,
        agent_id=agent_id,
    )
    if continuation_request:
        return delta_messages, delta_todos, {
            "invocationId": branch.get("invocationId"),
            "taskBriefId": branch.get("taskBriefId"),
            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
            "taskGoal": branch.get("reason"),
            "agentId": agent_id,
            "agentName": branch.get("agentName") or agent_id,
            "delegationId": branch.get("delegationId"),
            "lane": branch.get("lane") or "subagent",
            "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
            "targetLabel": branch.get("agentName") or agent_id,
            "branchIndex": branch.get("branchIndex"),
            "status": "waiting_input",
            "error": "runtime_input_required",
            "resultText": result_text,
            "requiredInputs": continuation_request["requiredInputs"],
            "continuationRequest": continuation_request,
            "completedAt": None,
            "messageCount": len(delta_messages),
            "todoDeltaCount": len(delta_todos),
            "toolMode": agent_data.get("tool_mode"),
            "toolsUsed": _extract_tool_names(delta_messages),
            "compactTranscript": _compact_transcript(delta_messages),
            "localSelfCheck": "The worker paused only for explicit missing input; execution evidence remains attached to the same runtime episode.",
            "acceptanceHint": "Supply the requested values and resume the same parent runtime episode; do not create a replacement route.",
        }, []
    reported_failure = (
        _subagent_governance_terminal_failure(delta_messages)
        or _subagent_reported_terminal_failure(result_text)
    )
    creative_evidence: dict[str, Any] | None = None
    if not reported_failure and _is_creative_runtime_execution_branch(branch):
        creative_evidence = _creative_tool_evidence(delta_messages, branch=branch)
        missing_creative_evidence = list(creative_evidence.get("missingEvidence") or [])
        if missing_creative_evidence:
            return delta_messages, delta_todos, {
                "invocationId": branch.get("invocationId"),
                "taskBriefId": branch.get("taskBriefId"),
                "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                "taskGoal": branch.get("reason"),
                "agentId": agent_id,
                "agentName": branch.get("agentName") or agent_id,
                "delegationId": branch.get("delegationId"),
                "lane": branch.get("lane") or "subagent",
                "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                "targetLabel": branch.get("agentName") or agent_id,
                "branchIndex": branch.get("branchIndex"),
                "status": "failed",
                "error": "creative_media_delivery_evidence_missing",
                "completedAt": _now_iso(),
                "messageCount": len(delta_messages),
                "todoDeltaCount": len(delta_todos),
                "toolMode": agent_data.get("tool_mode"),
                "toolsUsed": _extract_tool_names(delta_messages),
                "creativeExecutionEvidence": creative_evidence,
                "artifactRefs": list(creative_evidence.get("artifactRefs") or []),
                "proofRefs": list(creative_evidence.get("proofRefs") or []),
                "compactTranscript": _compact_transcript(delta_messages),
                "localSelfCheck": (
                    "The Creative worker exhausted two bounded in-branch corrections without both real artifact "
                    "and quality ToolMessage evidence."
                ),
                "acceptanceHint": "Repair or resume this same Creative runtime branch; do not accept planning prose as delivery.",
            }, []
    artifact_failure = (
        None
        if reported_failure
        else _validate_required_skill_artifacts(
            branch=branch,
            state=local_state,
            delta_messages=delta_messages,
        )
    )
    if artifact_failure:
        return delta_messages, delta_todos, {
            "invocationId": branch.get("invocationId"),
            "taskBriefId": branch.get("taskBriefId"),
            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
            "taskGoal": branch.get("reason"),
            "agentId": agent_id,
            "agentName": branch.get("agentName") or agent_id,
            "delegationId": branch.get("delegationId"),
            "lane": branch.get("lane") or "subagent",
            "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
            "targetLabel": branch.get("agentName") or agent_id,
            "branchIndex": branch.get("branchIndex"),
            "completedAt": _now_iso(),
            "messageCount": len(delta_messages),
            "todoDeltaCount": len(delta_todos),
            "toolMode": agent_data.get("tool_mode"),
            "toolsUsed": _extract_tool_names(delta_messages),
            **artifact_failure,
        }, []
    artifact_status = (
        None
        if reported_failure
        else _required_artifact_write_status(
            branch=branch,
            state=local_state,
            delta_messages=delta_messages,
            agent_data=agent_data,
        )
    )
    if artifact_status and (
        artifact_status.get("missingExpectedArtifacts")
        or artifact_status.get("missingRequiredTool")
    ):
        write_tool_succeeded = bool(artifact_status.get("writeToolSucceeded"))
        return delta_messages, delta_todos, {
            "invocationId": branch.get("invocationId"),
            "taskBriefId": branch.get("taskBriefId"),
            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
            "taskGoal": branch.get("reason"),
            "agentId": agent_id,
            "agentName": branch.get("agentName") or agent_id,
            "delegationId": branch.get("delegationId"),
            "lane": branch.get("lane") or "subagent",
            "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
            "targetLabel": branch.get("agentName") or agent_id,
            "branchIndex": branch.get("branchIndex"),
            "status": "blocked",
            "error": (
                "required_artifact_tool_not_called"
                if not write_tool_succeeded
                else "expected_artifact_not_ready"
            ),
            "completedAt": _now_iso(),
            "messageCount": len(delta_messages),
            "todoDeltaCount": len(delta_todos),
            "toolMode": agent_data.get("tool_mode"),
            "toolsUsed": _extract_tool_names(delta_messages),
            "compactTranscript": _compact_transcript(delta_messages),
            "localSelfCheck": (
                "The worker returned after bounded artifact corrections without both a successful write_native_file "
                "ToolMessage and ready expected artifacts. Pre-existing files or prose completion are not write evidence."
            ),
            "acceptanceHint": (
                "Retry the same delegated task with write_native_file visible and call it for every exact "
                "expected path before acceptance."
            ),
            **artifact_status,
        }, []
    verification_failure = (
        None
        if reported_failure
        else _validate_required_verification_evidence(
            branch=branch,
            delta_messages=delta_messages,
        )
    )
    if verification_failure:
        return delta_messages, delta_todos, {
            "invocationId": branch.get("invocationId"),
            "taskBriefId": branch.get("taskBriefId"),
            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
            "taskGoal": branch.get("reason"),
            "agentId": agent_id,
            "agentName": branch.get("agentName") or agent_id,
            "delegationId": branch.get("delegationId"),
            "lane": branch.get("lane") or "subagent",
            "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
            "targetLabel": branch.get("agentName") or agent_id,
            "branchIndex": branch.get("branchIndex"),
            "completedAt": _now_iso(),
            "messageCount": len(delta_messages),
            "todoDeltaCount": len(delta_todos),
            "toolMode": agent_data.get("tool_mode"),
            "toolsUsed": _extract_tool_names(delta_messages),
            "compactTranscript": _compact_transcript(delta_messages),
            **verification_failure,
        }, []
    verification_evidence: dict[str, Any] | None = None
    if _required_verification_tools(branch):
        verification_evidence, _missing_verification, _verification_mismatches = _verification_evidence_result(
            branch=branch,
            delta_messages=delta_messages,
        )
    final_artifact_snapshot = _artifact_progress_snapshot(expected_artifact_paths)
    initial_by_path = {item[0]: item for item in initial_artifact_snapshot}
    final_by_path = {item[0]: item for item in final_artifact_snapshot}
    existing_artifact_paths = [path for path in expected_artifact_paths if path.exists()]
    changed_artifact_paths = [
        path
        for path in existing_artifact_paths
        if final_by_path.get(str(path)) != initial_by_path.get(str(path))
    ]
    reported_status, reported_error = reported_failure or ("ok", "")
    summary = {
        "invocationId": branch.get("invocationId"),
        "taskBriefId": branch.get("taskBriefId"),
        "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
        "taskGoal": branch.get("reason"),
        "agentId": agent_id,
        "agentName": branch.get("agentName") or agent_id,
        "delegationId": branch.get("delegationId"),
        "lane": branch.get("lane") or "subagent",
        "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
        "targetLabel": branch.get("agentName") or agent_id,
        "branchIndex": branch.get("branchIndex"),
        "status": reported_status,
        **({"error": reported_error} if reported_error else {}),
        "completedAt": _now_iso(),
        "messageCount": len(delta_messages),
        "todoDeltaCount": len(delta_todos),
        "toolMode": agent_data.get("tool_mode"),
        "toolsUsed": _extract_tool_names(delta_messages),
        "availableTools": _available_tool_surface(agent_data),
        **(
            {
                "requiredTool": artifact_status.get("requiredTool"),
                "requiredToolVisible": artifact_status.get("requiredToolVisible"),
                "requiredToolChoice": artifact_status.get("requiredToolChoice"),
                "toolCallCount": artifact_status.get("toolCallCount"),
                "writeToolCallCount": artifact_status.get("writeToolCallCount"),
                "writeToolSucceeded": artifact_status.get("writeToolSucceeded"),
            }
            if artifact_status
            else {}
        ),
        "toolPolicy": dict((branch.get("taskBrief") or {}).get("toolPolicy") or {})
        if isinstance(branch.get("taskBrief"), dict)
        else {},
        "expectedOutputs": list((branch.get("taskBrief") or {}).get("expectedOutputs") or [])
        if isinstance(branch.get("taskBrief"), dict)
        else [],
        "behaviorScope": list((branch.get("taskBrief") or {}).get("behaviorScope") or [])
        if isinstance(branch.get("taskBrief"), dict)
        else [],
        "acceptanceContract": (branch.get("taskBrief") or {}).get("acceptanceContract")
        if isinstance(branch.get("taskBrief"), dict)
        else None,
        "resultText": result_text,
        "summary": _subagent_result_summary(delta_messages),
        "compactTranscript": _compact_transcript(delta_messages),
        "localSelfCheck": (
            "Subagent explicitly reported a terminal blocker/failure; parent must repair or retry before acceptance."
            if reported_failure
            else "Subagent branch completed; supervisor must still accept, retry, or ignore the result."
        ),
        "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
        "parentDelegationId": branch.get("parentDelegationId"),
        "parentInvocationId": branch.get("parentInvocationId"),
        "delegationDepth": branch.get("delegationDepth"),
        "artifactRefs": [
            {"path": str(path), "kind": "workspace_artifact"}
            for path in (changed_artifact_paths if reported_failure else existing_artifact_paths)
        ],
        "observedArtifactRefs": [
            {"path": str(path), "kind": "workspace_artifact"}
            for path in existing_artifact_paths
            if path not in changed_artifact_paths
        ],
        "missingExpectedArtifacts": [str(path) for path in expected_artifact_paths if not path.exists()],
        **(
            {
                "verificationEvidence": verification_evidence,
                "verificationResults": [
                    {
                        "status": "verified" if verification_evidence.get("passed") else "failed",
                        **verification_evidence,
                    }
                ],
            }
            if verification_evidence
            else {}
        ),
        "supervisorAcceptance": {"status": "pending", "requiredAction": ["accept", "retry", "ignore"]},
        "resultSchemaMatched": True,
        **(
            {
                "creativeExecutionEvidence": creative_evidence,
                "artifactRefs": list(creative_evidence.get("artifactRefs") or []),
                "proofRefs": list(creative_evidence.get("proofRefs") or []),
            }
            if creative_evidence
            else {}
        ),
    }
    _publish_parallel_progress(
        progress_callback,
        stage="handoff_blocked" if reported_failure else "handoff_ready",
        status=reported_status if reported_failure else "completed",
        summary=(
            f"{branch.get('agentName') or agent_id} 已回传阻塞信息，需修复后重试。"
            if reported_failure
            else f"{branch.get('agentName') or agent_id} 已回传可验收结果。"
        ),
    )
    return delta_messages, delta_todos, summary, []


def build_parallel_delegate_task_node(
    agent_nodes_map: dict[str, Any],
    *,
    resolve_agent_node: Callable[[str], Any | None] | None = None,
):
    async def parallel_delegate_task(state: dict[str, Any]) -> Command:
        branch = dict(state.get("parallel_branch") or {})
        agent_id = str(branch.get("agentId") or "")
        agent_data = agent_nodes_map.get(agent_id)
        if not agent_data and resolve_agent_node is not None:
            try:
                agent_data = resolve_agent_node(agent_id)
            except Exception:
                agent_data = None
        if not agent_data:
            return Command(
                goto="parallel_delegate_join",
                update={
                    "parallel_results": [
                        {
                            "invocationId": branch.get("invocationId"),
                            "taskBriefId": branch.get("taskBriefId"),
                            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                            "taskGoal": branch.get("reason"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "delegationId": branch.get("delegationId"),
                            "lane": branch.get("lane") or "subagent",
                            "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                            "targetLabel": branch.get("agentName") or agent_id,
                            "branchIndex": branch.get("branchIndex"),
                            "status": "error",
                            "error": "subagent_target_missing",
                            "summary": f"未找到子 Agent '{agent_id}'，该分支已回交 Supervisor。",
                            "registryVersion": branch.get("registryVersion"),
                            "registryHash": branch.get("registryHash"),
                            "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
                            "completedAt": _now_iso(),
                        }
                    ]
                },
            )

        emitted_progress: set[tuple[str, str]] = set()

        def _progress_callback(progress: dict[str, Any]) -> None:
            raw_stage = str(progress.get("stage") or "working").strip().lower()
            stage = "tool_execution" if raw_stage in {"tool_started", "tool_finished"} else raw_stage
            status = str(progress.get("status") or "running").strip().lower()
            timeline_node = progress.get("timelineNode") if isinstance(progress.get("timelineNode"), dict) else None
            timeline_identity = str((timeline_node or {}).get("id") or (timeline_node or {}).get("toolCallId") or "")
            timeline_revision = str(
                (timeline_node or {}).get("streamSequence")
                or (timeline_node or {}).get("finalized")
                or len(str((timeline_node or {}).get("content") or ""))
                or ""
            )
            fingerprint = (f"{stage}:{timeline_identity}:{timeline_revision}", status)
            if fingerprint in emitted_progress:
                return
            emitted_progress.add(fingerprint)
            episode_id = str(branch.get("delegationId") or branch.get("invocationId") or "").strip()
            summary = str(progress.get("summary") or "子代理正在处理任务。").strip()[:360]
            if episode_id:
                try:
                    heartbeat_runtime_episode(episode_id, progress=summary)
                except RuntimeEpisodeDurabilityError:
                    # Progress is telemetry. A stale lease must not be
                    # turned into a false durable heartbeat or abort the
                    # worker branch that is still producing its result.
                    pass
            runtime_context = _runtime_context_from_parallel_state(state, branch=branch)
            with bind_runtime_context(**runtime_context):
                emit_runtime_episode_event(
                    "runtime.episode.progress",
                    {
                        "episodeId": episode_id,
                        "kind": "delegation",
                        "state": "completed" if status == "completed" else "failed" if status == "failed" else "active",
                        "progress": {
                            "stage": stage,
                            "status": status,
                            "summary": summary,
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "delegationId": branch.get("delegationId"),
                            "taskBriefId": branch.get("taskBriefId"),
                            **({"timelineNode": timeline_node} if timeline_node else {}),
                        },
                    },
                    source={"runtime": "delegation", "component": "parallel_delegate_task"},
                )

        try:
            delta_messages, delta_todos, summary, child_requests = await _run_parallel_agent_branch(
                state,
                agent_data,
                progress_callback=_progress_callback,
            )
            summary = _finalize_managed_branch_workspace(branch, summary)
            return Command(
                goto="parallel_delegate_join",
                update={
                    "todos": delta_todos,
                    "parallel_results": [summary],
                    **({"pending_child_delegations": child_requests} if child_requests else {}),
                },
            )
        except Exception as exc:
            sandbox_failure = _fail_managed_branch_workspace(
                branch,
                str(getattr(exc, "code", None) or exc or exc.__class__.__name__),
            )
            _progress_callback(
                {
                    "stage": "failed",
                    "status": "failed",
                    "summary": f"{branch.get('agentName') or agent_id} 执行失败，已回传紧凑轨迹。",
                }
            )
            return Command(
                goto="parallel_delegate_join",
                update={
                    "parallel_results": [
                        {
                            "invocationId": branch.get("invocationId"),
                            "taskBriefId": branch.get("taskBriefId"),
                            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                            "taskGoal": branch.get("reason"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "delegationId": branch.get("delegationId"),
                            "lane": branch.get("lane") or "subagent",
                            "targetId": branch.get("targetId") or branch.get("ephemeralAgentId") or agent_id,
                            "targetLabel": branch.get("agentName") or agent_id,
                            "branchIndex": branch.get("branchIndex"),
                            "status": "error",
                            "error": str(exc).strip() or exc.__class__.__name__,
                            "compactTrace": str(getattr(exc, "compact_trace", "") or "")[:2400],
                            "toolsUsed": list(getattr(exc, "tools_used", []) or [])[:12],
                            **sandbox_failure,
                            "localSelfCheck": "Subagent branch failed before supervisor acceptance.",
                            "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
                            "completedAt": _now_iso(),
                        }
                    ],
                },
            )

    return parallel_delegate_task


def build_parallel_delegate_join_node():
    def _child_sends_for_invocation(state: dict[str, Any], invocation_id: str) -> tuple[list[Send], list[dict[str, Any]], list[dict[str, Any]]]:
        routed_request_ids = {str(item or "").strip() for item in list(state.get("routed_child_delegation_request_ids") or [])}
        pending = [
            dict(item)
            for item in list(state.get("pending_child_delegations") or [])
            if str(item.get("sourceInvocationId") or "").strip() == invocation_id
            and str(item.get("requestId") or "").strip() not in routed_request_ids
        ]
        sends: list[Send] = []
        invocation_counts: dict[str, int] = {}
        summaries: list[dict[str, Any]] = []
        seen_request_ids: set[str] = set()
        for item in pending:
            request_id = str(item.get("requestId") or "").strip()
            if request_id and request_id in seen_request_ids:
                continue
            if request_id:
                seen_request_ids.add(request_id)
            send_data = item.get("send") if isinstance(item.get("send"), dict) else {}
            node = str(send_data.get("node") or "").strip()
            arg = send_data.get("arg")
            if node != "parallel_delegate_task" or not isinstance(arg, dict):
                continue
            branch = dict(arg.get("parallel_branch") or {})
            child_invocation_id = str(branch.get("invocationId") or item.get("childInvocationId") or "").strip()
            if not child_invocation_id:
                continue
            invocation_counts[child_invocation_id] = invocation_counts.get(child_invocation_id, 0) + 1
            sends.append(Send("parallel_delegate_task", arg))
            summaries.append(
                {
                    "requestId": request_id,
                    "sourceInvocationId": invocation_id,
                    "sourceDelegationId": item.get("sourceDelegationId"),
                    "sourceAgentId": item.get("sourceAgentId"),
                    "sourceAgentName": item.get("sourceAgentName"),
                    "childInvocationId": child_invocation_id,
                    "childDelegationId": branch.get("delegationId") or item.get("childDelegationId"),
                    "childTaskBriefId": branch.get("taskBriefId") or item.get("childTaskBriefId"),
                    "childTaskGoal": branch.get("reason") or item.get("childTaskGoal"),
                    "childTaskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else item.get("childTaskBrief"),
                    "childAgentId": branch.get("agentId") or item.get("childAgentId"),
                    "childAgentName": branch.get("agentName") or item.get("childAgentName"),
                    "childDepth": branch.get("delegationDepth") or item.get("childDepth"),
                    "childBranch": branch,
                    "state": "routed",
                    "createdAt": item.get("createdAt") or _now_iso(),
                }
            )
        invocation_records = [
            {
                "invocationId": child_invocation_id,
                "expected": expected,
                "createdAt": _now_iso(),
                "parentInvocationId": invocation_id,
                "source": "child_delegation_router",
            }
            for child_invocation_id, expected in invocation_counts.items()
        ]
        return sends, invocation_records, summaries

    def parallel_delegate_join(state: dict[str, Any]) -> Command:
        invocations = list(state.get("parallel_invocations") or [])
        latest = invocations[-1] if invocations else {}
        invocation_id = str(latest.get("invocationId") or "").strip()
        expected = int(latest.get("expected") or 0)
        results = [
            dict(item)
            for item in list(state.get("parallel_results") or [])
            if str(item.get("invocationId") or "").strip() == invocation_id
        ]
        if not results:
            return Command(goto="supervisor", update={})

        child_sends, child_invocations, child_summaries = _child_sends_for_invocation(state, invocation_id)
        if child_sends:
            route_context = dict(state.get("current_route_context") or {})
            session_id = str(state.get("session_id") or state.get("sessionId") or route_context.get("session_id") or route_context.get("sessionId") or "").strip() or None
            run_id = str(state.get("run_id") or state.get("runId") or route_context.get("run_id") or route_context.get("runId") or "").strip() or None
            workspace_path = str(state.get("workspace_path") or state.get("workspacePath") or route_context.get("workspace_path") or route_context.get("workspacePath") or "").strip() or None
            child_episodes: list[dict[str, Any]] = []
            for child_summary in child_summaries:
                child_branch = dict(child_summary.get("childBranch") or {})
                worker_brief = dict(child_summary.get("childTaskBrief") or {})

                def _set_default_text(key: str, *values: Any) -> None:
                    if str(worker_brief.get(key) or "").strip():
                        return
                    for value in values:
                        text = str(value or "").strip()
                        if text:
                            worker_brief[key] = text
                            return

                _set_default_text("id", child_summary.get("childTaskBriefId"), child_summary.get("childInvocationId"))
                _set_default_text("taskBriefId", child_summary.get("childTaskBriefId"), worker_brief.get("id"))
                _set_default_text("title", child_summary.get("childTaskGoal"), worker_brief.get("goal"), "child delegation")
                _set_default_text("goal", child_summary.get("childTaskGoal"), worker_brief.get("brief"), worker_brief.get("title"), "Continue the requested child delegation.")
                _set_default_text("brief", worker_brief.get("goal"), child_summary.get("childTaskGoal"), "Continue the requested child delegation.")
                _set_default_text("agentId", child_summary.get("childAgentId"))
                _set_default_text("agentName", child_summary.get("childAgentName"))
                if not worker_brief.get("runtimeAccess"):
                    worker_brief["runtimeAccess"] = child_branch.get("runtimeAccess") or []
                worker_brief.setdefault("parentDelegationId", child_summary.get("sourceDelegationId"))
                worker_brief.setdefault("parentInvocationId", invocation_id)
                worker_brief.setdefault("writeSet", child_branch.get("writeSet"))
                worker_brief.setdefault("acceptanceHint", child_branch.get("acceptanceHint"))
                if workspace_path:
                    worker_brief.setdefault("workspacePath", workspace_path)
                child_delegation_id = str(child_summary.get("childDelegationId") or "").strip()
                existing_episode = db.get_runtime_episode(child_delegation_id) if child_delegation_id else None
                expected_parent_id = str(child_summary.get("sourceDelegationId") or invocation_id or "").strip()
                if existing_episode and str(
                    existing_episode.get("parentEpisodeId") or existing_episode.get("parent_episode_id") or ""
                ).strip() != expected_parent_id:
                    existing_episode = None
                episode = existing_episode or build_runtime_episode(
                    need={
                        "kind": "delegation",
                        "source": "subagent",
                        "reason": child_summary.get("childTaskGoal") or "child delegation",
                        "needId": child_delegation_id or child_summary.get("childInvocationId"),
                        "parentEpisodeId": child_summary.get("sourceDelegationId") or invocation_id,
                        "inputs": {
                            "targetCount": 1,
                            "workerBriefs": [worker_brief],
                            "allowChildDelegation": bool(child_branch.get("allowChildDelegation")),
                            "childDelegationBudget": child_branch.get("childDelegationBudget") or {},
                            "writeSetPartitions": child_branch.get("writeSetPartitions") or [],
                            **({"workspacePath": workspace_path} if workspace_path else {}),
                        },
                    },
                    kind="delegation",
                    state="queued",
                    required_runtime_access=[],
                    parent_episode_id=expected_parent_id,
                    continuation_target="runtime_episode_runner",
                    extra={
                        "sourceInvocationId": invocation_id,
                        "sourceAgentId": child_summary.get("sourceAgentId"),
                        "sourceAgentName": child_summary.get("sourceAgentName"),
                        "childInvocationId": child_summary.get("childInvocationId"),
                        "childTaskBriefId": child_summary.get("childTaskBriefId"),
                        "childAgentId": child_summary.get("childAgentId"),
                        "childAgentName": child_summary.get("childAgentName"),
                        "childDepth": child_summary.get("childDepth"),
                        **({"workspacePath": workspace_path} if workspace_path else {}),
                    },
                )
                with bind_runtime_context(
                    session_id=session_id,
                    run_id=run_id,
                    workspace_path=workspace_path,
                    runtime_kind="delegation",
                    trigger_source="child_delegation_router",
                ):
                    queued_episode = enqueue_runtime_episode(episode, session_id=session_id, run_id=run_id, priority=45)
                route_context = upsert_runtime_episode(route_context, queued_episode)
                child_episodes.append(queued_episode)
                with bind_runtime_context(
                    session_id=session_id,
                    run_id=run_id,
                    workspace_path=workspace_path,
                    runtime_kind="delegation",
                    trigger_source="child_delegation_router",
                ):
                    emit_runtime_episode_event("delegation.child.requested", {"episode": queued_episode, "childDelegation": child_summary})
                    emit_runtime_episode_event("runtime.episode.queued", {"episode": queued_episode})
            child_episode_ids = [
                str(item.get("episodeId") or item.get("id") or "").strip()
                for item in child_episodes
                if str(item.get("episodeId") or item.get("id") or "").strip()
            ]
            source_delegation_ids = {
                str(item.get("sourceInvocationId") or "").strip(): str(item.get("sourceDelegationId") or "").strip()
                for item in child_summaries
                if str(item.get("sourceInvocationId") or "").strip()
                and str(item.get("sourceDelegationId") or "").strip()
            }
            for item in results:
                status = str(item.get("status") or "").strip().lower()
                if status not in {"waiting", "waiting_child", "waiting_child_delegation", "waiting_dependency"}:
                    continue
                producer_episode_id = str(
                    item.get("delegationId")
                    or source_delegation_ids.get(str(item.get("invocationId") or "").strip())
                    or item.get("invocationId")
                    or invocation_id
                    or ""
                ).strip()
                if not producer_episode_id:
                    continue
                worker_contract = build_delegation_result_contract(item)
                worker_status = worker_contract.pop("status", status)
                waiting_handoff = build_handoff_ref(
                    producer_episode_id=producer_episode_id,
                    kind="subagent_result",
                    status="waiting",
                    compact_summary=(
                        f"{item.get('agentName') or item.get('targetLabel') or 'Direct subagent'} "
                        f"has routed {len(child_episode_ids)} child delegation(s); final acceptance is pending child handoff."
                    ),
                    consumer_hint=(
                        "This is an execution-progress handoff, not an acceptance result. "
                        "Wait for the child episode terminal handoff; do not poll or redispatch."
                    ),
                    extra={
                        **worker_contract,
                        "workerStatus": worker_status,
                        "childEpisodeIds": child_episode_ids,
                        "delegationState": "waiting_child",
                    },
                )
                persisted_waiting_handoff = persist_handoff_ref(
                    waiting_handoff,
                    session_id=session_id,
                    run_id=run_id,
                )
                if isinstance(persisted_waiting_handoff, dict):
                    waiting_handoff = {**waiting_handoff, **persisted_waiting_handoff}
                route_context = append_handoff_ref(route_context, waiting_handoff)
                route_context, parent_episode = transition_runtime_episode(
                    route_context,
                    producer_episode_id,
                    state="waiting_child",
                    resultRef=waiting_handoff.get("handoffRefId"),
                    childEpisodeIds=child_episode_ids,
                )
                if parent_episode:
                    persist_runtime_episode(
                        parent_episode,
                        session_id=session_id,
                        run_id=run_id,
                        enqueue=False,
                    )
                    emit_runtime_episode_event(
                        "runtime.episode.waiting",
                        {"episode": parent_episode, "handoffRef": waiting_handoff},
                    )
                emit_runtime_episode_event("handoff.ref.created", {"handoffRef": waiting_handoff})
            return Command(
                goto=RUNTIME_EPISODE_WAIT_NODE,
                update={
                    "messages": [
                        HumanMessage(
                            content=(
                                "[V8OS 孙 Agent 执行中]\n"
                                f"直接子 Agent 已提交 {len(child_episodes)} 个孙 Agent 任务，但当前状态只是 waiting_child_delegation，"
                                "不是可验收结果。此时不得 accept/retry/ignore，也不得根据任务说明猜测孙 Agent 输出。\n"
                                "请结束当前执行片段，不要调用 wait 或 observe 轮询；孙 Agent 终态后系统会携带真实结构化回流恢复本任务。"
                            ),
                            id=str(uuid.uuid4()),
                            additional_kwargs={
                                "v8_governance_type": "delegation_child_pending",
                                "v8_delegation_invocation_id": invocation_id,
                                "v8_child_episode_ids": child_episode_ids,
                            },
                        )
                    ],
                    "routed_child_delegation_request_ids": [
                        *list(state.get("routed_child_delegation_request_ids") or []),
                        *[str(item.get("requestId") or "") for item in child_summaries if item.get("requestId")],
                    ],
                    "current_route_context": merge_route_context(
                        route_context,
                        {
                            "lastChildDelegationRouted": {
                                "parentInvocationId": invocation_id,
                                "childCount": len(child_sends),
                                "childDelegations": child_summaries[-10:],
                                "childEpisodeIds": child_episode_ids,
                                "routedAt": _now_iso(),
                            }
                        },
                    ),
                },
            )

        waiting_results = [
            item
            for item in results
            if str(item.get("status") or "").strip().lower()
            in {"waiting", "waiting_child", "waiting_child_delegation", "waiting_dependency"}
        ]
        if waiting_results:
            return Command(
                goto="supervisor",
                update={
                    "current_route_context": merge_route_context(
                        dict(state.get("current_route_context") or {}),
                        {
                            "lastDelegationHandoff": {
                                "invocationId": invocation_id,
                                "state": "waiting_child",
                                "pendingResultCount": len(waiting_results),
                                "updatedAt": _now_iso(),
                            }
                        },
                    )
                },
            )

        route_context = dict(state.get("current_route_context") or {})
        session_id = str(
            state.get("session_id")
            or state.get("sessionId")
            or route_context.get("session_id")
            or route_context.get("sessionId")
            or ""
        ).strip() or None
        run_id = str(
            state.get("run_id")
            or state.get("runId")
            or route_context.get("run_id")
            or route_context.get("runId")
            or ""
        ).strip() or None
        integration_context: dict[str, Any] = {}
        candidate_results = [
            item
            for item in results
            if _delegation_summary_allows_changeset_promotion(item)
            and isinstance(item.get("gitChangeSet"), dict)
            and not isinstance(item.get("parentWorktreeMerge"), dict)
            and int(item.get("delegationDepth") or 1) <= 1
        ]
        failed_results = [
            item
            for item in results
            if not _delegation_summary_allows_changeset_promotion(item)
        ]
        if run_id and candidate_results and not failed_results:
            from core.engineering_sandbox.service import get_engineering_sandbox_service

            try:
                integration_worktree, integration_change_set = (
                    get_engineering_sandbox_service().build_run_integration(
                        run_id=run_id,
                        invocation_id=invocation_id,
                        change_sets=[dict(item.get("gitChangeSet") or {}) for item in candidate_results],
                    )
                )
                integration_context = {
                    "workspace_path": integration_worktree.topology.worktree_workspace_root,
                    "original_workspace_path": integration_worktree.topology.original_workspace_root,
                    "repository_root": integration_worktree.topology.repository_root,
                    "worktree_root": integration_worktree.topology.worktree_root,
                    "worktree_id": integration_worktree.worktree_id,
                    "managedIntegration": integration_change_set.as_dict(),
                }
                for item in candidate_results:
                    item["integrationChangeSet"] = integration_change_set.as_dict()
            except Exception as exc:
                error_code = str(getattr(exc, "code", None) or str(exc) or exc.__class__.__name__).strip()
                for item in candidate_results:
                    item["status"] = "error"
                    item["error"] = f"managed_integration_failed:{error_code}"
                    item["localSelfCheck"] = (
                        "The isolated task commit is preserved, but deterministic integration failed. "
                        "Supervisor must repair the conflict before acceptance."
                    )
                    item["integrationEvidence"] = {"state": "blocked", "errorCode": error_code}
        handoff_refs: list[dict[str, Any]] = []
        handoff_contracts: list[dict[str, Any]] = []
        for item in results:
            worker_contract = build_delegation_result_contract(item)
            handoff_contracts.append(worker_contract)
            result_contract = dict(worker_contract)
            worker_status = result_contract.pop("status", None)
            if worker_status:
                result_contract["workerStatus"] = worker_status
            producer_episode_id = str(item.get("delegationId") or item.get("invocationId") or invocation_id or "").strip()
            compact = str(item.get("compactTranscript") or item.get("localSelfCheck") or item.get("error") or item.get("taskGoal") or "").strip()
            compact = (
                f"Delegation {'completed' if item.get('status') == 'ok' else 'failed'}: "
                f"{compact or producer_episode_id or invocation_id}"
            )
            handoff = build_handoff_ref(
                producer_episode_id=producer_episode_id,
                kind="subagent_result",
                status="failed" if item.get("status") != "ok" else "ready",
                compact_summary=compact,
                detail_tool=(
                    "delegation_broker(mode='observe', delegation_id="
                    f"'{str(item.get('delegationId') or invocation_id or '').strip()}')"
                ),
                consumer_hint=str(item.get("acceptanceHint") or "Supervisor should accept, retry, or ignore this delegated result."),
                extra=result_contract,
            )
            persisted_handoff = persist_handoff_ref(
                handoff,
                session_id=session_id,
                run_id=run_id,
            )
            if isinstance(persisted_handoff, dict):
                handoff = {**handoff, **persisted_handoff}
            route_context = append_handoff_ref(route_context, handoff)
            route_context, episode = transition_runtime_episode(
                route_context,
                producer_episode_id,
                state="completed" if item.get("status") == "ok" else "failed",
                resultRef=handoff.get("handoffRefId"),
            )
            if episode:
                persist_runtime_episode(
                    episode,
                    session_id=session_id,
                    run_id=run_id,
                    enqueue=False,
                )
            handoff_refs.append(handoff)
            emit_runtime_episode_event("handoff.ref.created", {"handoffRef": handoff})
            if episode:
                emit_runtime_episode_event(
                    "runtime.episode.completed" if item.get("status") == "ok" else "runtime.episode.failed",
                    {"episode": episode, "handoffRef": handoff},
                )
        summary = _render_delegation_handoff_message(
            invocation_id=invocation_id,
            expected=expected,
            contracts=handoff_contracts,
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [summary],
                "current_route_context": merge_route_context(
                    route_context,
                    {
                        "lastDelegationHandoff": {
                            "invocationId": invocation_id,
                            "handoffRefs": [item.get("handoffRefId") for item in handoff_refs],
                            "results": handoff_contracts,
                            "completedAt": _now_iso(),
                        },
                        **integration_context,
                    },
                ),
                **({"workspace_path": integration_context.get("workspace_path")} if integration_context else {}),
                **({"original_workspace_path": integration_context.get("original_workspace_path")} if integration_context else {}),
                **({"repository_root": integration_context.get("repository_root")} if integration_context else {}),
                **({"worktree_root": integration_context.get("worktree_root")} if integration_context else {}),
                **({"worktree_id": integration_context.get("worktree_id")} if integration_context else {}),
            },
        )

    return parallel_delegate_join
