from __future__ import annotations

import re
from typing import Any

from core.json_safe import to_jsonable


DELEGATION_ACCEPTANCE_DECISION_RE = re.compile(
    r"(?:验收(?:决定|结论|结果|动作)|acceptance\s+(?:decision|conclusion|result|action))"
    r"\s*[`*_~]*\s*[：:]\s*(?:(?:显式|明确)\s*)?[`*_~]*\s*"
    r"(ACCEPT|RETRY|IGNORE)\b\s*[`*_~]*",
    re.IGNORECASE,
)

DELEGATION_ACCEPTANCE_HEADING_RE = re.compile(
    r"(?:^|\n)\s{0,3}(?:#{1,6}\s*)?"
    r"(?:验收动作|acceptance\s+action)\s*(?:\r?\n)+\s*"
    r"[`*_~\s]*(ACCEPT|RETRY|IGNORE)\b[`*_~\s]*"
    r"(?=[：:\-—–]|$)",
    re.IGNORECASE,
)


def parse_delegation_acceptance_text(
    value: Any,
    *,
    summary_limit: int = 600,
) -> dict[str, str] | None:
    text = str(value or "").strip()
    matches = sorted(
        [
            *DELEGATION_ACCEPTANCE_DECISION_RE.finditer(text),
            *DELEGATION_ACCEPTANCE_HEADING_RE.finditer(text),
        ],
        key=lambda match: match.start(),
    )
    decisions = {
        str(match.group(1) or "").strip().upper()
        for match in matches
        if str(match.group(1) or "").strip()
    }
    if len(decisions) != 1:
        return None
    decision = next(iter(decisions))
    status = {
        "ACCEPT": "accepted",
        "RETRY": "retry",
        "IGNORE": "ignored",
    }.get(decision)
    if not status:
        return None
    evidence_basis = text[matches[-1].end():].strip()
    evidence_basis = re.sub(r"^[`*\s>—–:：-]+", "", evidence_basis)
    if len(evidence_basis) > summary_limit:
        evidence_basis = f"{evidence_basis[: max(0, summary_limit - 1)].rstrip()}…"
    return {
        "status": status,
        "decision": decision,
        "summary": evidence_basis or f"Supervisor recorded {decision} for the delegated result.",
    }


def _compact(value: Any, *, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)].rstrip() + "\n...[truncated]"


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value in (None, "", {}):
        return []
    return [value]


def build_delegation_result_contract(result: dict[str, Any]) -> dict[str, Any]:
    """Project one child result without dropping acceptance or lineage evidence."""

    item = dict(result or {})
    task_brief = item.get("taskBrief") if isinstance(item.get("taskBrief"), dict) else {}
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    status = str(item.get("status") or "unknown").strip() or "unknown"
    local_self_check = _compact(item.get("localSelfCheck"), limit=1200)
    acceptance_hint = _compact(
        item.get("acceptanceHint")
        or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
        limit=900,
    )
    artifact_refs = _list(item.get("artifactRefs") or item.get("artifacts"))
    missing_artifact_evidence = list(
        dict.fromkeys(
            str(value).strip()
            for value in [
                *_list(item.get("missingArtifacts")),
                *_list(item.get("missingExpectedArtifacts")),
                *_list(item.get("sparseArtifacts")),
            ]
            if str(value).strip()
        )
    )
    supervisor_acceptance = item.get("supervisorAcceptance")
    if not isinstance(supervisor_acceptance, dict):
        supervisor_acceptance = {
            "status": "pending",
            "requiredAction": ["accept", "retry", "ignore"],
        }
    required_values = (
        item.get("taskBriefId") or task_brief.get("taskBriefId") or task_brief.get("id"),
        item.get("delegationId") or item.get("id"),
        item.get("targetId") or item.get("agentId"),
        status,
        local_self_check,
        acceptance_hint,
    )
    result_schema_matched = item.get("resultSchemaMatched")
    if result_schema_matched is None:
        result_schema_matched = all(bool(value) for value in required_values)

    contract = {
        "contractVersion": "delegation-result/v1",
        "taskBriefId": item.get("taskBriefId") or task_brief.get("taskBriefId") or task_brief.get("id"),
        "delegationId": item.get("delegationId") or item.get("id"),
        "parentDelegationId": (
            item.get("parentDelegationId")
            or task_brief.get("parentDelegationId")
            or context.get("parentDelegationId")
        ),
        "parentInvocationId": (
            item.get("parentInvocationId")
            or task_brief.get("parentInvocationId")
            or context.get("parentInvocationId")
        ),
        "delegationDepth": item.get("delegationDepth") or task_brief.get("delegationDepth") or context.get("delegationDepth"),
        "invocationId": item.get("invocationId"),
        "targetId": item.get("targetId") or item.get("agentId"),
        "targetLabel": item.get("targetLabel") or item.get("agentName") or item.get("targetId") or item.get("agentId"),
        "agentId": item.get("agentId"),
        "agentName": item.get("agentName"),
        "lane": item.get("lane"),
        "status": status,
        "error": item.get("error"),
        "dispatchStatus": item.get("dispatchStatus"),
        "artifactRefs": to_jsonable(artifact_refs),
        "missingArtifactEvidence": missing_artifact_evidence,
        "localSelfCheck": local_self_check,
        "acceptanceHint": acceptance_hint,
        "supervisorAcceptance": supervisor_acceptance,
        "resultSchemaMatched": bool(result_schema_matched),
        "gitChangeSet": to_jsonable(item.get("gitChangeSet"))
        if isinstance(item.get("gitChangeSet"), dict)
        else None,
        "sandboxEvidence": to_jsonable(item.get("sandboxEvidence"))
        if isinstance(item.get("sandboxEvidence"), dict)
        else None,
        "integrationChangeSet": to_jsonable(item.get("integrationChangeSet"))
        if isinstance(item.get("integrationChangeSet"), dict)
        else None,
        "integrationEvidence": to_jsonable(item.get("integrationEvidence"))
        if isinstance(item.get("integrationEvidence"), dict)
        else None,
        "parentWorktreeMerge": to_jsonable(item.get("parentWorktreeMerge"))
        if isinstance(item.get("parentWorktreeMerge"), dict)
        else None,
        "verificationEvidence": to_jsonable(item.get("verificationEvidence"))
        if isinstance(item.get("verificationEvidence"), dict)
        else None,
        "verificationResults": to_jsonable(item.get("verificationResults"))
        if isinstance(item.get("verificationResults"), list)
        else None,
        "missingVerificationTools": _list(item.get("missingVerificationTools")),
        "verificationEvidenceMismatches": _list(item.get("verificationEvidenceMismatches")),
        "toolsUsed": list(item.get("toolsUsed") or item.get("toolNames") or []),
        "toolPolicy": dict(item.get("toolPolicy") or task_brief.get("toolPolicy") or {})
        if isinstance(item.get("toolPolicy") or task_brief.get("toolPolicy") or {}, dict)
        else {},
        "expectedOutputs": _list(item.get("expectedOutputs") or task_brief.get("expectedOutputs")),
        "behaviorScope": _list(item.get("behaviorScope") or task_brief.get("behaviorScope")),
        "acceptanceContract": item.get("acceptanceContract") or task_brief.get("acceptanceContract"),
        "resultText": str(item.get("resultText") or "").strip(),
        "summary": _compact(item.get("summary") or item.get("compactTranscript") or item.get("taskGoal"), limit=900),
        "compactTranscript": _compact(item.get("compactTranscript"), limit=1200),
    }
    return {key: value for key, value in contract.items() if value not in (None, "", [], {})}
