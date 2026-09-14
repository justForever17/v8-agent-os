from __future__ import annotations

from typing import Any

from core.json_safe import to_jsonable


def delegation_handoff_results(handoff: dict) -> list[dict]:
    payload = handoff.get("payload") if isinstance(handoff.get("payload"), dict) else handoff
    nested = payload.get("delegationHandoff") or {}
    results = payload.get("results") or nested.get("results") or ([payload] if payload.get("taskBriefId") else [])
    return [item for item in results if isinstance(item, dict)]


def delegation_result_acceptance(episode: dict, handoff: dict, task_brief_id: str) -> dict:
    """Read only the parent's decision bound to this immutable result version."""
    payload = handoff.get("payload") if isinstance(handoff.get("payload"), dict) else handoff
    current_ref = episode.get("resultRef") or episode.get("result_ref")
    head = (episode.get("metadata") or {}).get("supervisorAcceptance") or {}
    handoff_ref = payload.get("handoffRefId") or payload.get("handoffId")
    if (current_ref and current_ref == handoff_ref == head.get("handoffRefId")
            and payload.get("payloadDigest") and payload["payloadDigest"] == head.get("payloadDigest")):
        decision = (head.get("results") or {}).get(task_brief_id)
        if isinstance(decision, dict):
            return dict(decision)
    return {"status": "pending", "requiredAction": ["accept", "retry", "ignore"]}


def delegation_result_has_execution_gap(result: dict) -> bool:
    verification = result.get("verificationEvidence") or {}
    return bool(
        str(result.get("workerStatus") or result.get("status") or "unknown").lower() not in {"ok", "ready", "completed", "success", "done"}
        or verification.get("passed") is False
        or result.get("missingVerificationTools") or result.get("verificationEvidenceMismatches")
        or result.get("missingArtifactEvidence") or result.get("missingExpectedArtifacts")
        or (result.get("executionContractRepair") or {}).get("required")
    )


def resolved_delegation_retries(episodes: list[dict], handoffs_by_episode: dict) -> dict[str, set[str]]:
    """Resolve only exact retry versions connected by durable dispatch provenance.

    Old receipts stay immutable. A replacement must itself be reviewed, and a
    newer unreviewed/failed result cannot inherit an earlier version's vote.
    """
    current = {}
    rows = {str(row.get("episodeId") or row.get("id") or ""): row for row in episodes}
    for episode_id, row in rows.items():
        ref = row.get("resultRef") or row.get("result_ref")
        for handoff in handoffs_by_episode.get(episode_id, []):
            payload = handoff.get("payload") if isinstance(handoff.get("payload"), dict) else handoff
            if handoff.get("payloadCorrupted") or not payload.get("payloadDigest"):
                continue
            if not ref or ref != (payload.get("handoffRefId") or payload.get("handoffId")):
                continue
            for result in delegation_handoff_results(payload):
                task = str(result.get("taskBriefId") or "")
                key = (episode_id, ref, payload["payloadDigest"], task)
                current[key] = delegation_result_acceptance(row, payload, task).get("status")
    resolved = {key for key, status in current.items() if status in {"accepted", "ignored"}}
    replacements = set()
    changed = True
    while changed:
        changed = False
        for key in list(resolved):
            row = rows[key[0]]
            for source in (row.get("metadata") or {}).get("retryOfResults", []):
                old_key = tuple(source.get(field) for field in ("episodeId", "handoffRefId", "payloadDigest", "taskBriefId"))
                old = rows.get(old_key[0], {})
                if (old_key in resolved or current.get(old_key) != "retry" or old_key[3] != key[3]
                        or any((old.get(field) or "") != (row.get(field) or "")
                               for field in ("session_id", "run_id", "parentEpisodeId"))):
                    continue
                resolved.add(old_key)
                replacements.add(old_key)
                changed = True
    result = {}
    for key in replacements:
        result.setdefault(key[0], set()).add(key[3])
    return result


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
        "errorCode": item.get("errorCode"),
        "providerErrorCode": item.get("providerErrorCode"),
        "errorMessage": _compact(item.get("errorMessage"), limit=600),
        "dispatchStatus": item.get("dispatchStatus"),
        "governanceWait": to_jsonable(item.get("governanceWait")),
        "effectReceipt": to_jsonable(item.get("effectReceipt")),
        "requiredInputs": to_jsonable(item.get("requiredInputs"))
        if isinstance(item.get("requiredInputs"), list)
        else None,
        "continuationRequest": to_jsonable(item.get("continuationRequest"))
        if isinstance(item.get("continuationRequest"), dict)
        else None,
        "artifactRefs": to_jsonable(artifact_refs),
        "artifactRefsAccepted": item.get("artifactRefsAccepted"),
        "proofRefs": to_jsonable(_list(item.get("proofRefs"))),
        "creativeExecutionEvidence": to_jsonable(item.get("creativeExecutionEvidence"))
        if isinstance(item.get("creativeExecutionEvidence"), dict)
        else None,
        "missingArtifactEvidence": missing_artifact_evidence,
        "localSelfCheck": local_self_check,
        "repairAction": _compact(item.get("repairAction"), limit=900),
        "workerReportedSummary": _compact(item.get("workerReportedSummary"), limit=900),
        "workerReportedResultText": _compact(item.get("workerReportedResultText"), limit=1200),
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
        "executionContractRepair": to_jsonable(item.get("executionContractRepair")),
        "toolsUsed": list(item.get("toolsUsed") or item.get("toolNames") or []),
        "availableTools": _list(item.get("availableTools")),
        "requiredTool": item.get("requiredTool"),
        "requiredToolVisible": item.get("requiredToolVisible"),
        "requiredToolChoice": item.get("requiredToolChoice"),
        "toolCallCount": item.get("toolCallCount"),
        "writeToolCallCount": item.get("writeToolCallCount"),
        "writeToolSucceeded": item.get("writeToolSucceeded"),
        "missingRequiredTool": item.get("missingRequiredTool"),
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
