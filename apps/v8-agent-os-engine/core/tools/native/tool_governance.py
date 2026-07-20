from __future__ import annotations

import hashlib
import json
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
_HARD_REVIEW_RISK_TOKENS = (
    "blocked",
    "credential",
    "database",
    "destructive",
    "download_execute",
    "encoded",
    "financial",
    "firewall",
    "hotkey",
    "package_install",
    "persistence",
    "privilege",
    "process",
    "profile",
    "protected",
    "recent_download",
    "secret",
    "sensitive",
)
_HARD_REVIEW_TARGETS = {
    "private_data_exfiltration",
    "v8_integrity",
    "extensions_integrity",
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


def _decision_details_text(decision: SafetyDecision) -> str:
    details = decision.details if isinstance(decision.details, dict) else {}
    values = [
        decision.risk_code,
        decision.governance_target,
        details.get("path"),
        details.get("command"),
        details.get("url"),
        details.get("target"),
        details.get("matched_command"),
    ]
    return " ".join(str(value or "").lower() for value in values)


def safety_review_is_hard_stop(decision: SafetyDecision) -> bool:
    if decision.is_block() or not decision.allow_override:
        return True
    risk_code = str(decision.risk_code or "").strip().lower()
    governance_target = str(decision.governance_target or "").strip().lower()
    if governance_target in _HARD_REVIEW_TARGETS:
        return True
    text = _decision_details_text(decision)
    return any(token in risk_code or token in text for token in _HARD_REVIEW_RISK_TOKENS)


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
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"safety:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


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

    allowlist_entry = safety_guardian.is_allowlisted(decision)
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
    request_payload["allowlistCandidate"] = safety_guardian.build_allowlist_candidate(decision)

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
