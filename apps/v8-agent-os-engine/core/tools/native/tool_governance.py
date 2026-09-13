from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from langgraph.types import Interrupt as LangGraphInterrupt

from core.database import db
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import SafetyDecision, safety_guardian

try:
    from langgraph.errors import GraphBubbleUp, GraphInterrupt, Interrupt as ErrorInterrupt, NodeInterrupt

    LANGGRAPH_INTERRUPT_EXCEPTIONS = tuple(
        interrupt_type
        for interrupt_type in (GraphBubbleUp, GraphInterrupt, ErrorInterrupt, NodeInterrupt, LangGraphInterrupt)
        if interrupt_type is not None
    )
except Exception:  # pragma: no cover - defensive fallback for older langgraph builds
    LANGGRAPH_INTERRUPT_EXCEPTIONS = (LangGraphInterrupt,)


_LANGGRAPH_INTERRUPT_CLASS_NAMES = {
    "GraphBubbleUp",
    "GraphInterrupt",
    "Interrupt",
    "NodeInterrupt",
}

SAFETY_APPROVAL_MODES = {"manual", "reduced", "minimal"}
_REDUCED_AUTO_APPROVE_RISK_CODES = {
    "review_host",
    "external_mutating_http",
    "trusted_provider_api_http",
    "computer_use_mutation",
}
_HARD_REVIEW_RISK_CODES = frozenset({
    "credential_exfiltration_http", "unknown_credential_host_http",
    "encoded_command_review", "sensitive_system_read_command",
    "windows_profile_sensitive_read", "windows_profile_registry_mutation",
    "windows_profile_acl_mutation", "windows_profile_reparse_mutation",
    "windows_profile_hive_mutation", "windows_profile_destructive_copy",
    "linux_auth_store_mutation", "linux_sensitive_read",
    "macos_account_store_mutation", "macos_keychain_sensitive_read",
    "cross_platform_sensitive_read", "cross_platform_sensitive_system_mutation",
})
_HARD_REVIEW_TARGETS = {
    "private_data_exfiltration",
    "v8_integrity",
}


def _is_langgraph_interrupt(value: Any, *, _depth: int = 0) -> bool:
    if _depth > 4 or value is None:
        return False

    if LANGGRAPH_INTERRUPT_EXCEPTIONS and isinstance(value, LANGGRAPH_INTERRUPT_EXCEPTIONS):
        return True

    if value.__class__.__name__ in _LANGGRAPH_INTERRUPT_CLASS_NAMES:
        return True

    if isinstance(value, BaseException):
        return any(_is_langgraph_interrupt(item, _depth=_depth + 1) for item in value.args)

    if isinstance(value, (list, tuple, set)):
        return any(_is_langgraph_interrupt(item, _depth=_depth + 1) for item in value)

    if isinstance(value, dict):
        interrupt_keys = {"approvalKind", "approval_kind", "interactionKind", "interaction_kind", "question", "prompt", "toolCallId", "tool_call_id"}
        if any(key in value for key in interrupt_keys):
            return True
        return any(_is_langgraph_interrupt(item, _depth=_depth + 1) for item in value.values())

    nested_value = getattr(value, "value", None)
    if nested_value is not None and nested_value is not value:
        return _is_langgraph_interrupt(nested_value, _depth=_depth + 1)

    return False


def _raise_langgraph_interrupt_if_needed(exc: Exception) -> None:
    if _is_langgraph_interrupt(exc):
        raise exc


def _raise_runtime_governance_exception_if_needed(exc: Exception) -> None:
    if isinstance(exc, ModelGovernanceInterventionRequired):
        raise exc
    _raise_langgraph_interrupt_if_needed(exc)


def normalize_safety_approval_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in SAFETY_APPROVAL_MODES else "manual"


def current_safety_approval_mode() -> str:
    runtime_context = get_runtime_context()
    return normalize_safety_approval_mode(
        runtime_context.get("safety_approval_mode")
        or runtime_context.get("safetyApprovalMode")
    )


def safety_review_is_hard_stop(decision: SafetyDecision) -> bool:
    if decision.is_block() or not decision.allow_override:
        return True
    risk_code = str(decision.risk_code or "").strip().lower()
    governance_target = str(decision.governance_target or "").strip().lower()
    if governance_target in _HARD_REVIEW_TARGETS:
        return True
    # Classification belongs to the guardian. Words inside an ordinary path,
    # URL or command (e.g. /profile or process-report.md) are not risk evidence.
    return risk_code in _HARD_REVIEW_RISK_CODES


def should_auto_approve_safety_review(decision: SafetyDecision, *, mode: str | None = None) -> bool:
    normalized_mode = normalize_safety_approval_mode(mode or current_safety_approval_mode())
    if normalized_mode == "manual" or not decision.is_review() or safety_review_is_hard_stop(decision):
        return False
    risk_code = str(decision.risk_code or "").strip().lower()
    if normalized_mode == "reduced":
        return risk_code in _REDUCED_AUTO_APPROVE_RISK_CODES
    return True


def log_safety_review_auto_approved(
    decision: SafetyDecision,
    *,
    action: str,
    subject: str,
    tool_call_id: str = "",
    mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    resolved_mode = normalize_safety_approval_mode(mode or current_safety_approval_mode())
    safety_guardian.log_decision_event(
        action=f"{action}_auto_approved",
        decision=decision,
        subject=subject,
        metadata={
            "toolCallId": tool_call_id,
            "safetyApprovalMode": resolved_mode,
            **(metadata or {}),
        },
    )


def _safety_operation_fingerprint(
    decision: SafetyDecision,
    *,
    tool_call_id: str = "",
    include_tool_call_id: bool = True,
) -> str:
    details = decision.details if isinstance(decision.details, dict) else {}
    runtime_context = details.get("runtime_context") if isinstance(details.get("runtime_context"), dict) else {}
    target = (
        details.get("path")
        or details.get("command")
        or details.get("url")
        or details.get("target")
        or details.get("pid")
        or ""
    )
    payload = {
        "runId": str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip(),
        "riskCode": decision.risk_code,
        "governanceTarget": decision.governance_target,
        "target": str(target).strip(),
        # Controlled OS actions are never authorized by a previous command with
        # the same text. Both replay keys bind their immutable one-shot request.
        "operationId": str(details.get("operationId") or ""),
        "sandboxLeaseId": str(
            runtime_context.get("sandbox_lease_id") or runtime_context.get("sandboxLeaseId") or ""
        ).strip(),
        "sandboxPolicyDigest": str(
            runtime_context.get("sandbox_policy_digest")
            or runtime_context.get("sandboxPolicyDigest")
            or ""
        ).strip(),
        "worktreeId": str(runtime_context.get("worktree_id") or runtime_context.get("worktreeId") or "").strip(),
    }
    sandbox_policy = runtime_context.get("sandbox_policy") or runtime_context.get("sandboxPolicy")
    if isinstance(sandbox_policy, dict):
        payload.update(
            {
                "baseCommit": str(
                    sandbox_policy.get("base_commit") or sandbox_policy.get("baseCommit") or ""
                ).strip(),
                "writeSet": list(sandbox_policy.get("write_set") or sandbox_policy.get("writeSet") or []),
                "networkProfile": str(
                    sandbox_policy.get("network_profile") or sandbox_policy.get("networkProfile") or ""
                ).strip(),
            }
        )
    if include_tool_call_id:
        payload["toolCallId"] = str(tool_call_id or "").strip()
    if "operationArguments" in details:
        payload["operationArguments"] = details["operationArguments"]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"safety:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def workspace_safety_decision(
    decision: SafetyDecision, *, preflight: dict[str, Any], runtime_context: dict[str, Any],
    tool_name: str, arguments: dict[str, Any],
) -> SafetyDecision:
    """Bind one native invocation to Safety without changing its workspace or grants."""
    from core.workspace_capability import extract_absolute_paths_from_command, resolve_workspace_tool_path

    context = dict(runtime_context)
    paths = [str(preflight.get("resolvedPath") or "")]
    if "command" in arguments:
        paths = [str(resolve_workspace_tool_path(path, runtime_context=context).get("resolvedPath") or path)
                 for path in extract_absolute_paths_from_command(str(arguments["command"]))]
    cwd = str(preflight.get("resolvedCwd") or preflight.get("cwd") or context.get("command_cwd") or "")
    scope = {"tool": tool_name, **arguments, "resolvedTargets": paths, "cwd": cwd}
    details = {**decision.details, "runtime_context": context, "operationArguments": scope}
    outside = not preflight.get("ok") or bool(preflight.get("hostAccess"))
    if outside:
        details["exactApprovalRequired"] = True
        details["workspaceAccess"] = {"targets": paths, "cwd": cwd, "activeWorkspaceRoot": preflight.get("binding", {}).get("activeWorkspaceRoot")}
        if decision.is_allow() or (decision.is_review() and not safety_review_is_hard_stop(decision)):
            details.pop("eventSummary", None)
            return safety_guardian._decision(verdict="review", risk_code="workspace_external_access",
                governance_target="external_workspace", reason="本次操作访问当前工作区以外的路径，请确认具体目标和参数。",
                posture=decision.posture, details=details)
    return replace(decision, details=details)


def _is_safety_operation_previously_approved(
    operation_fingerprint: str,
    operation_target_fingerprint: str = "",
) -> bool:
    runtime_context = get_runtime_context()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    if not run_id:
        return False
    run_record = db.get_run_record(run_id)
    if not run_record:
        return False
    operations = (run_record.get("metadata") or {}).get("approvedSafetyOperations")
    if not isinstance(operations, list):
        return False
    candidates = {str(operation_fingerprint or "").strip(), str(operation_target_fingerprint or "").strip()}
    candidates.discard("")
    for item in operations:
        if not isinstance(item, dict):
            continue
        if str(item.get("fingerprint") or "") in candidates:
            return True
        if str(item.get("targetFingerprint") or item.get("operationTargetFingerprint") or "") in candidates:
            return True
    return False


def _enforce_safety_decision(
    decision: SafetyDecision,
    *,
    tool_call_id: str,
    question: str,
) -> tuple[bool, str | None]:
    operation_fingerprint = _safety_operation_fingerprint(decision, tool_call_id=tool_call_id)
    operation_target_fingerprint = _safety_operation_fingerprint(decision, tool_call_id="", include_tool_call_id=False)
    safety_guardian.log_decision_event(
        action="native_tool_safety",
        decision=decision,
        subject=question,
        metadata={
            "toolCallId": tool_call_id,
            "operationFingerprint": operation_fingerprint,
            "operationTargetFingerprint": operation_target_fingerprint,
        },
    )
    if decision.is_allow():
        return True, None

    if decision.is_block() or not decision.allow_override:
        return False, f"Safety Guardian 已阻止该操作：{decision.reason}"

    controlled_operation = bool((decision.details or {}).get("operationId") or (decision.details or {}).get("exactApprovalRequired"))
    if (decision.details or {}).get("exactApprovalRequired"):
        context = (decision.details or {}).get("runtime_context") or {}
        run_id = str(context.get("run_id") or context.get("runId") or "")
        run = db.get_run_record(run_id) if run_id else None
        if run and str(run.get("status") or "") in {"cancelled", "aborted", "failed", "error"}:
            return False, "当前执行已经结束或取消，未执行跨工作区操作。"
    allowlist_entry = None if controlled_operation else safety_guardian.is_allowlisted(decision)
    if allowlist_entry:
        safety_guardian.log_decision_event(
            action="native_tool_safety_allowlist_reused",
            decision=decision,
            subject=question,
            metadata={
                "toolCallId": tool_call_id,
                "allowlistEntryId": allowlist_entry.get("id"),
            },
        )
        return True, None

    if operation_fingerprint and _is_safety_operation_previously_approved(operation_fingerprint, operation_target_fingerprint):
        safety_guardian.log_decision_event(
            action="native_tool_safety_approval_reused",
            decision=decision,
            subject=question,
            metadata={
                "toolCallId": tool_call_id,
                "operationFingerprint": operation_fingerprint,
                "operationTargetFingerprint": operation_target_fingerprint,
            },
        )
        return True, None

    if should_auto_approve_safety_review(decision):
        log_safety_review_auto_approved(
            decision,
            action="native_tool_safety",
            subject=question,
            tool_call_id=tool_call_id,
            metadata={
                "operationFingerprint": operation_fingerprint,
                "operationTargetFingerprint": operation_target_fingerprint,
            },
        )
        return True, None

    request_payload = decision.to_interrupt_request(question=question, tool_call_id=tool_call_id)
    if operation_fingerprint:
        request_payload["operationFingerprint"] = operation_fingerprint
    if operation_target_fingerprint:
        request_payload["operationTargetFingerprint"] = operation_target_fingerprint
    request_payload["allowlistCandidate"] = None if controlled_operation else safety_guardian.build_allowlist_candidate(decision)

    raise ModelGovernanceInterventionRequired(
        f"Safety Guardian 检测到治理审批请求：{decision.reason}",
        approval_kind="safety_review",
        question=question,
        details={
            "safety": decision.to_payload(),
            "toolCallId": tool_call_id,
            "operationFingerprint": operation_fingerprint,
            "operationTargetFingerprint": operation_target_fingerprint,
        },
        request_payload=request_payload,
    )
