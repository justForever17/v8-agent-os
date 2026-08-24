from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from core.database import db
from core.delegation_result_contract import parse_delegation_acceptance_text
from core.runtime_episodes import (
    ACTIVE_EPISODE_STATES,
    resolve_runtime_episode_current_handoff,
    runtime_episode_parent_id,
    superseded_runtime_episode_ids,
)


RUNTIME_EXECUTION_HANDOFF_STATUSES = {"ready", "degraded"}
_PSEUDO_SIDE_EFFECT_TOOL_NAMES = {
    "write_native_file",
    "write_file",
    "run_system_command",
    "runtime_broker",
    "spec_broker",
    "creative_media_assets",
    "creative_media_jobs",
    "creative_media_edit",
}


def _pseudo_side_effect_tool_names(text: str) -> list[str]:
    """Detect textual tool markup that a provider failed to emit structurally.

    Keep the invariant narrow: ordinary prose mentioning a tool is allowed,
    while an XML/DSML-shaped invocation of a side-effect tool cannot be
    accepted as execution evidence.
    """

    normalized = str(text or "")
    if not re.search(r"<\s*tool_call\b", normalized, flags=re.IGNORECASE):
        return []
    names = {
        match.group(1).strip()
        for match in re.finditer(
            r"<\s*invoke\b[^>]*\bname\s*=\s*['\"]([^'\"]+)['\"]",
            normalized,
            flags=re.IGNORECASE,
        )
        if match.group(1).strip()
    }
    return sorted(name for name in names if name in _PSEUDO_SIDE_EFFECT_TOOL_NAMES)


@dataclass(frozen=True, slots=True)
class SupervisorCompletionDecision:
    action: str = "complete"
    reason: str = "eligible"
    details: dict[str, Any] = field(default_factory=dict)


def _is_optional_episode(episode: Mapping[str, Any]) -> bool:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    metadata = episode.get("metadata") if isinstance(episode.get("metadata"), Mapping) else {}
    return any(
        bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
        for source in (episode, inputs, metadata)
    ) or str(inputs.get("dependencyMode") or metadata.get("dependencyMode") or "").strip().lower() in {
        "optional",
        "degraded_ok",
    }


def _looks_forward_only(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized or len(normalized) > 700:
        return False
    forward_markers = (
        "开始",
        "接下来",
        "我将",
        "我会",
        "现在我重新",
        "现在让我",
        "准备启动",
        "准备开始",
        "正在启动",
        "starting",
        "i will",
        "next i",
    )
    result_markers = (
        "已完成",
        "完成并回流",
        "已回流",
        "交付",
        "结果",
        "证据",
        "来源",
        "限制",
        "缺少",
        "无法",
        "失败",
        "降级",
        "degraded",
        "completed",
        "ready",
    )
    return any(marker in normalized for marker in forward_markers) and not any(
        marker in normalized for marker in result_markers
    )


def _has_ready_runtime_handoff(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> bool:
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            status = str(handoff.get("status") or "").strip().lower()
            if status in RUNTIME_EXECUTION_HANDOFF_STATUSES:
                return True
    return False


def _required_runtime_degraded_handoffs(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    degraded: list[dict[str, Any]] = []
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            status = str(handoff.get("status") or "").strip().lower()
            if status == "degraded":
                degraded.append(
                    {
                        "episodeId": episode_id,
                        "handoffRefId": handoff.get("handoffRefId"),
                        "kind": handoff.get("kind"),
                        "status": status,
                    }
                )
    return degraded


def _delegation_acceptance_missing(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    final_text: str,
) -> list[str]:
    if parse_delegation_acceptance_text(final_text):
        return []
    pending: list[str] = []
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        if str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip():
            continue
        if str(episode.get("kind") or "").strip().lower() != "delegation":
            continue
        state = str(episode.get("state") or "").strip().lower()
        if state not in {"completed", "merged", "degraded"}:
            continue
        metadata = episode.get("metadata") if isinstance(episode.get("metadata"), Mapping) else {}
        acceptance = metadata.get("supervisorAcceptance") if isinstance(metadata.get("supervisorAcceptance"), Mapping) else {}
        acceptance_status = str(acceptance.get("status") or "").strip().lower()
        if acceptance_status in {"accepted", "retry", "ignored"}:
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        has_terminal_handoff = any(
            str(handoff.get("status") or "").strip().lower() in RUNTIME_EXECUTION_HANDOFF_STATUSES
            for handoff in list(handoffs_by_episode.get(episode_id, []) or [])
            if isinstance(handoff, Mapping)
        )
        if has_terminal_handoff:
            pending.append(episode_id)
    return pending


def _spec_tasks_need_proof(spec_brief: Mapping[str, Any]) -> list[dict[str, Any]]:
    traceability = spec_brief.get("traceability") if isinstance(spec_brief.get("traceability"), Mapping) else {}
    tasks = [dict(item) for item in list(traceability.get("tasks") or []) if isinstance(item, Mapping)]
    return [
        task
        for task in tasks
        if str(task.get("proofRequired") or task.get("independentAcceptance") or "").strip()
    ]


def _handoff_has_verifiable_proof(handoff: Mapping[str, Any]) -> bool:
    payload = handoff.get("payload") if isinstance(handoff.get("payload"), Mapping) else {}
    refs = handoff.get("refs") if isinstance(handoff.get("refs"), list) else payload.get("refs")
    if isinstance(refs, list) and any(str(item or "").strip() for item in refs):
        return True
    if str(handoff.get("raw_ref") or handoff.get("rawRef") or "").strip():
        return True
    if str(handoff.get("detail_tool") or handoff.get("detailTool") or "").strip():
        return True
    text = " ".join(
        str(value or "")
        for value in (
            handoff.get("compact_summary"),
            handoff.get("compactSummary"),
            handoff.get("summary"),
            payload.get("compactSummary"),
            payload.get("summary"),
            payload.get("proof"),
            payload.get("acceptance"),
            payload.get("verification"),
        )
    ).lower()
    return any(
        marker in text
        for marker in (
            "proof",
            "verified",
            "verification",
            "acceptance",
            "evidence",
            "artifact",
            "changed file",
            "touched file",
            "证明",
            "验收",
            "验证",
            "证据",
            "产物",
            "文件",
        )
    )


def _missing_spec_proof_handoffs(
    spec_brief: Mapping[str, Any],
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    required_tasks = _spec_tasks_need_proof(spec_brief)
    if not required_tasks:
        return None
    ready_handoffs: list[dict[str, Any]] = []
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            status = str(handoff.get("status") or "").strip().lower()
            if status in RUNTIME_EXECUTION_HANDOFF_STATUSES:
                ready_handoffs.append(dict(handoff))
    if any(_handoff_has_verifiable_proof(handoff) for handoff in ready_handoffs):
        return None
    return {
        "taskIds": [str(task.get("taskId") or "") for task in required_tasks[:8] if str(task.get("taskId") or "").strip()],
        "handoffCount": len(ready_handoffs),
        "message": "Approved Spec execution returned runtime handoff(s), but no verifiable proof/acceptance refs were found.",
    }


def _episode_task_briefs(episode: Mapping[str, Any]) -> list[dict[str, Any]]:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    raw = inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or []
    return [dict(item) for item in list(raw or []) if isinstance(item, Mapping)]


def _brief_requires_write(brief: Mapping[str, Any], *, episode_kind: str) -> bool:
    if bool(brief.get("readOnly") or brief.get("read_only")):
        return False
    capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), Mapping) else {}
    capsule_mode = str(capsule.get("executionMode") or capsule.get("execution_mode") or "").strip().lower()
    if capsule_mode in {"read_only", "verify", "plan_only"}:
        return False
    if bool(brief.get("writeRequired") or brief.get("write_required")):
        return True
    if list(brief.get("writeSet") or brief.get("write_set") or []):
        return True
    capabilities = " ".join(str(item or "") for item in list(brief.get("requiredCapabilities") or [])).lower()
    tool_policy = brief.get("toolPolicy") if isinstance(brief.get("toolPolicy"), Mapping) else {}
    allowed_tools = {
        str(item or "").strip()
        for item in list(tool_policy.get("allowedTools") or brief.get("allowedTools") or [])
        if str(item or "").strip()
    }
    return episode_kind == "engineering" or "write_native_file" in allowed_tools or any(
        marker in capabilities for marker in ("workspace_mutation", "file_write", "implementation")
    )


def _required_write_episode(episode: Mapping[str, Any]) -> bool:
    if _is_optional_episode(episode):
        return False
    kind = str(episode.get("kind") or "").strip().lower()
    briefs = _episode_task_briefs(episode)
    if briefs:
        return any(_brief_requires_write(brief, episode_kind=kind) for brief in briefs)
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    if bool(inputs.get("readOnly") or inputs.get("read_only")):
        return False
    return bool(inputs.get("writeRequired") or inputs.get("write_required") or kind == "engineering")


def _handoff_payload(handoff: Mapping[str, Any]) -> dict[str, Any]:
    payload = handoff.get("payload") if isinstance(handoff.get("payload"), Mapping) else {}
    return {**dict(handoff), **dict(payload)}


def _delegation_result_is_optional(result: Mapping[str, Any]) -> bool:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    if result.get("required") is False or metadata.get("required") is False:
        return True
    return any(
        bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
        or str(source.get("dependencyMode") or "").strip().lower()
        in {"optional", "degraded_ok"}
        for source in (result, metadata)
    )


def _delegation_result_block_reason(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or result.get("workerStatus") or "").strip().lower()
    if status in {
        "error",
        "failed",
        "blocked",
        "dependency_failed",
        "cancelled",
        "canceled",
        "recoverable_failed",
    }:
        return f"status:{status}"
    is_research_result = bool(
        result.get("answer")
        or result.get("researchRef")
        or result.get("evidenceBundleId")
        or result.get("sourceUrls")
    )
    accepted_research_result = bool(
        is_research_result
        and result.get("acceptancePassed") is True
        and str(result.get("qualityTier") or "").strip() == "high_quality"
        and str(result.get("answer") or "").strip()
    )
    if status == "degraded" and not accepted_research_result:
        return "status:degraded"
    if is_research_result and not accepted_research_result:
        return "research_result_not_accepted"
    if str(result.get("error") or result.get("errorCode") or result.get("errorMessage") or "").strip():
        return "typed_error"
    sandbox = result.get("sandboxEvidence") if isinstance(result.get("sandboxEvidence"), Mapping) else {}
    sandbox_state = str(sandbox.get("state") or "").strip().lower()
    if sandbox_state in {"failed", "merge_failed"}:
        return f"sandbox:{sandbox_state}"
    if result.get("artifactRefsAccepted") is False:
        return "artifact_refs_rejected"
    if "acceptancePassed" in result and result.get("acceptancePassed") is False:
        return "acceptance_failed"
    return ""


def _required_nested_delegation_failures(
    handoff: Mapping[str, Any],
    *,
    episode_kind: str,
) -> list[dict[str, Any]]:
    payload = _handoff_payload(handoff)
    handoff_kind = str(payload.get("kind") or "").strip().lower()
    has_delegation_surface = bool(
        episode_kind == "delegation"
        or "delegation" in handoff_kind
        or isinstance(payload.get("delegationHandoff"), Mapping)
        or list(payload.get("childHandoffs") or [])
    )
    if not has_delegation_surface:
        return []

    results: list[dict[str, Any]] = []
    visited: set[int] = set()

    def _collect(value: Any, *, depth: int = 0) -> None:
        if depth > 8 or not isinstance(value, Mapping) or id(value) in visited:
            return
        visited.add(id(value))
        for key in ("results", "taskBriefResults"):
            for item in list(value.get(key) or []):
                if not isinstance(item, Mapping):
                    continue
                results.append(dict(item))
                _collect(item, depth=depth + 1)
        nested = value.get("delegationHandoff")
        if isinstance(nested, Mapping):
            _collect(nested, depth=depth + 1)
        for child in list(value.get("childHandoffs") or []):
            if isinstance(child, Mapping):
                _collect(child, depth=depth + 1)
        nested_payload = value.get("payload")
        if isinstance(nested_payload, Mapping):
            _collect(nested_payload, depth=depth + 1)

    _collect(payload)
    failures: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for result in results:
        reason = _delegation_result_block_reason(result)
        if not reason or _delegation_result_is_optional(result):
            continue
        task_brief_id = str(result.get("taskBriefId") or result.get("taskId") or "").strip()
        delegation_id = str(result.get("delegationId") or result.get("invocationId") or "").strip()
        status = str(result.get("status") or result.get("workerStatus") or "failed").strip()
        error = str(result.get("error") or result.get("errorMessage") or "").strip()
        key = (task_brief_id, delegation_id, status, error or reason)
        if key in seen:
            continue
        seen.add(key)
        sandbox = result.get("sandboxEvidence") if isinstance(result.get("sandboxEvidence"), Mapping) else {}
        failures.append(
            {
                "taskBriefId": task_brief_id,
                "delegationId": delegation_id,
                "status": status,
                "reason": reason,
                "error": error[:900],
                "errorCode": str(result.get("errorCode") or sandbox.get("errorCode") or "").strip(),
                "repairAction": str(result.get("repairAction") or sandbox.get("repairAction") or "").strip()[:900],
            }
        )
    return failures


def _current_runtime_handoffs(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any] | None]:
    """Project one current delivery per required terminal episode.

    Older attempts remain durable history, but they must not override the
    episode's result_ref during Supervisor acceptance. A single legacy handoff
    remains readable when no result_ref was recorded; multiple unreferenced
    deliveries are ambiguous and are surfaced instead of guessed.
    """

    current: dict[str, list[dict[str, Any]]] = {}
    terminal_states = {"completed", "merged", "degraded", "failed", "cancelled"}
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        state = str(episode.get("state") or "").strip().lower()
        if not episode_id or state not in terminal_states:
            continue
        handoffs = [
            dict(item)
            for item in list(handoffs_by_episode.get(episode_id, []) or [])
            if isinstance(item, Mapping)
        ]
        selected, diagnostic = resolve_runtime_episode_current_handoff(
            episode,
            handoffs,
        )
        if selected is not None:
            current[episode_id] = [selected]
            continue
        resolution = str(diagnostic.get("resolution") or "missing_handoff")
        episode_error_code = str(diagnostic.get("episodeErrorCode") or "").strip()
        episode_error_message = str(diagnostic.get("episodeErrorMessage") or "").strip()
        if (
            state in {"failed", "cancelled"}
            and episode_error_code
            and resolution in {"missing_handoff", "result_ref_not_found"}
        ):
            return current, {
                "reason": episode_error_code,
                "episodeId": episode_id,
                "state": state,
                "episodeErrorMessage": episode_error_message,
                "deliveryResolution": resolution,
                "expectedResultRef": diagnostic.get("resultRef") or "",
                "availableHandoffIds": list(diagnostic.get("availableHandoffIds") or [])[:12],
                "recoverable": True,
                "nextAction": "repair_the_runtime_failure_then_retry_the_episode",
            }
        if resolution == "current_handoff_payload_corrupted":
            return current, {
                "reason": "runtime_handoff_payload_corrupted",
                "episodeId": episode_id,
                "state": state,
                "expectedResultRef": diagnostic.get("resultRef") or "",
                "availableHandoffIds": list(diagnostic.get("availableHandoffIds") or [])[:12],
                "payloadIntegrity": dict(diagnostic.get("payloadIntegrity") or {}),
                "recoverable": False,
                "nextAction": "restore_the_exact_durable_runtime_delivery",
            }
        if resolution == "missing_handoff":
            return current, {
                "reason": (
                    "required_runtime_episode_failed_without_handoff"
                    if state in {"failed", "cancelled"}
                    else "required_runtime_handoff_missing"
                ),
                "episodeId": episode_id,
                "state": state,
                "expectedResultRef": "",
                "availableHandoffIds": [],
                "recoverable": True,
                "nextAction": "recover_or_retry_the_missing_runtime_delivery",
            }
        if resolution == "result_ref_not_found":
            return current, {
                "reason": "runtime_result_handoff_missing",
                "episodeId": episode_id,
                "state": state,
                "expectedResultRef": diagnostic.get("resultRef") or "",
                "availableHandoffIds": list(diagnostic.get("availableHandoffIds") or [])[:12],
                "matchingHandoffCount": 0,
                "recoverable": True,
                "nextAction": "reload_or_retry_the_exact_runtime_delivery",
            }
        if resolution == "producer_mismatch":
            return current, {
                "reason": "runtime_result_handoff_producer_mismatch",
                "episodeId": episode_id,
                "state": state,
                "expectedResultRef": diagnostic.get("resultRef") or "",
                "availableHandoffIds": list(diagnostic.get("availableHandoffIds") or [])[:12],
                "recoverable": False,
                "nextAction": "restore_the_delivery_with_its_original_lineage",
            }
        return current, {
            "reason": "runtime_result_handoff_ambiguous",
            "episodeId": episode_id,
            "state": state,
            "expectedResultRef": diagnostic.get("resultRef") or "",
            "availableHandoffIds": list(diagnostic.get("availableHandoffIds") or [])[:12],
            "matchingHandoffCount": len(handoffs),
            "recoverable": True,
            "nextAction": "bind_the_episode_result_ref_to_one_delivery",
        }
    return current, None


def _collect_named_values(value: Any, keys: set[str], *, limit: int = 64) -> list[Any]:
    collected: list[Any] = []

    def _walk(item: Any) -> None:
        if len(collected) >= limit:
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key) in keys:
                    values = child if isinstance(child, list) else [child]
                    for candidate in values:
                        if candidate not in (None, "") and candidate not in collected:
                            collected.append(candidate)
                            if len(collected) >= limit:
                                return
                if isinstance(child, (Mapping, list, tuple)):
                    _walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                _walk(child)

    _walk(value)
    return collected


def _ref_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("path", "filePath", "file_path", "sourcePath", "workspaceRelativePath", "uri", "url", "ref"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _looks_like_file_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith(("artifact://", "workspace://")):
        return True
    if text.startswith("file://"):
        return True
    return bool(re.search(r"(?:^|[\\/])[^\\/]+\.[A-Za-z0-9]{1,12}$", text) or re.search(r"^[^\\/]+\.[A-Za-z0-9]{1,12}$", text))


def _existing_file_evidence(episode: Mapping[str, Any], handoffs: Iterable[Mapping[str, Any]]) -> list[str]:
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    engineering_workspace = (
        inputs.get("engineeringWorkspace")
        if isinstance(inputs.get("engineeringWorkspace"), Mapping)
        else {}
    )
    workspace_paths: list[Path] = []
    for workspace in (
        inputs.get("originalWorkspacePath"),
        inputs.get("original_workspace_path"),
        engineering_workspace.get("originalWorkspacePath"),
        engineering_workspace.get("original_workspace_path"),
        inputs.get("workspacePath"),
        inputs.get("workspace_path"),
    ):
        text = str(workspace or "").strip()
        if not text:
            continue
        try:
            candidate = Path(text).resolve()
        except Exception:
            continue
        if candidate not in workspace_paths:
            workspace_paths.append(candidate)
    values: list[Any] = []
    keys = {
        "artifactRefs",
        "artifacts",
        "changedPaths",
        "changed_paths",
        "changedFiles",
        "changed_files",
        "touchedFiles",
        "touched_files",
        "writtenFiles",
        "written_files",
        "outputFiles",
        "output_files",
    }
    for handoff in handoffs:
        values.extend(_collect_named_values(_handoff_payload(handoff), keys))
    evidence: list[str] = []
    episode_session_id = str(episode.get("sessionId") or episode.get("session_id") or "").strip()
    episode_run_id = str(episode.get("runId") or episode.get("run_id") or "").strip()
    for value in values:
        text = _ref_text(value)
        if not text or not _looks_like_file_path(text):
            continue
        if text.startswith("artifact://"):
            artifact_id = text[len("artifact://") :].strip("/\\")
            artifact = db.get_runtime_artifact(artifact_id) if artifact_id else None
            if not artifact:
                continue
            artifact_session_id = str(artifact.get("sessionId") or artifact.get("session_id") or "").strip()
            artifact_run_id = str(artifact.get("runId") or artifact.get("run_id") or "").strip()
            if episode_session_id and artifact_session_id and artifact_session_id != episode_session_id:
                continue
            if episode_run_id and artifact_run_id and artifact_run_id != episode_run_id:
                continue
            source_path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
            if source_path:
                try:
                    candidate = Path(source_path)
                    if candidate.exists() and candidate.is_file():
                        evidence.append(str(candidate.resolve()))
                except Exception:
                    pass
            continue
        candidate_text = text
        if text.startswith("workspace://"):
            if not workspace_paths:
                continue
            candidate_text = text[len("workspace://") :].lstrip("/\\")
        elif text.startswith("file://"):
            candidate_text = text[7:]
        try:
            candidate = Path(candidate_text)
            candidates = [candidate] if candidate.is_absolute() else [root / candidate for root in workspace_paths]
            for resolved_candidate in candidates:
                if resolved_candidate.exists() and resolved_candidate.is_file():
                    evidence.append(str(resolved_candidate.resolve()))
                    break
        except Exception:
            continue
    evidence.extend(_authoritative_agent_write_artifacts(episode, workspace_paths))
    return list(dict.fromkeys(evidence))[:32]


def _normalized_contract_path(value: Any) -> str:
    text = str(value or "").strip().strip("`'\"").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return re.sub(r"/+", "/", text).lstrip("/")


def _episode_write_set(episode: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for brief in _episode_task_briefs(episode):
        capsule = (
            brief.get("engineeringTaskCapsule")
            if isinstance(brief.get("engineeringTaskCapsule"), Mapping)
            else {}
        )
        raw_values = capsule.get("writeSet") if capsule else brief.get("writeSet") or brief.get("write_set")
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        for item in list(raw_values or []):
            normalized = _normalized_contract_path(item)
            if normalized and normalized not in values:
                values.append(normalized)
    return values


def _write_set_covers_path(relative_path: str, write_set: Iterable[str]) -> bool:
    candidate = _normalized_contract_path(relative_path).casefold()
    if not candidate:
        return False
    for raw_pattern in write_set:
        pattern = _normalized_contract_path(raw_pattern).casefold()
        if not pattern:
            continue
        if any(marker in pattern for marker in ("*", "?", "[")):
            if fnmatch(candidate, pattern):
                return True
            continue
        if candidate == pattern or candidate.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _authoritative_agent_write_artifacts(
    episode: Mapping[str, Any],
    workspace_paths: Iterable[Path],
) -> list[str]:
    """Resolve direct-workspace writes from the governed artifact ledger.

    A direct Engineering write is recorded even when a delegated handoff keeps
    only compact proof. Only the exact session/run-bound, authoritative
    ``write_native_file`` record may close this evidence gap. Managed worktree
    candidates remain ineligible until their ordinary merge handoff proves
    delivery to the original workspace.
    """

    session_id = str(episode.get("sessionId") or episode.get("session_id") or "").strip()
    run_id = str(episode.get("runId") or episode.get("run_id") or "").strip()
    write_set = _episode_write_set(episode)
    roots = [Path(item).resolve() for item in workspace_paths]
    if not session_id or not run_id or not write_set or not roots:
        return []
    try:
        artifacts = db.list_runtime_artifacts(session_id=session_id, run_id=run_id, limit=200)
    except Exception:
        return []

    evidence: list[str] = []
    for artifact in list(artifacts or []):
        if not isinstance(artifact, Mapping):
            continue
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
        if str(artifact.get("resourceRole") or artifact.get("resource_role") or "artifact").strip() != "artifact":
            continue
        if str(artifact.get("sessionId") or artifact.get("session_id") or "").strip() != session_id:
            continue
        if str(artifact.get("runId") or artifact.get("run_id") or "").strip() != run_id:
            continue
        if str(artifact.get("origin") or metadata.get("origin") or "").strip() != "agent_file_write":
            continue
        source_component = str(
            artifact.get("sourceComponent")
            or artifact.get("source_component")
            or metadata.get("source")
            or ""
        ).strip()
        if source_component != "write_native_file":
            continue
        if str(metadata.get("storageClass") or metadata.get("storage_class") or "").strip() != "workspace":
            continue
        if str(metadata.get("pathPlane") or metadata.get("path_plane") or "").strip() != "workspace_artifact":
            continue
        if str(metadata.get("deliveryState") or metadata.get("delivery_state") or "").strip() != "authoritative":
            continue
        if metadata.get("managedExecution") is not False and metadata.get("managed_execution") is not False:
            continue

        source_path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
        if not source_path:
            continue
        try:
            resolved = Path(source_path).resolve()
        except Exception:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        relative = _normalized_contract_path(
            metadata.get("workspaceRelativePath") or metadata.get("workspace_relative_path")
        )
        matching_root = next((root for root in roots if _path_is_within(resolved, root)), None)
        if matching_root is None:
            continue
        if not relative:
            try:
                relative = resolved.relative_to(matching_root).as_posix()
            except (OSError, ValueError):
                continue
        if not _write_set_covers_path(relative, write_set):
            continue
        evidence.append(str(resolved))
    return list(dict.fromkeys(evidence))[:32]


def _typed_creative_artifact_requirements(
    episode: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Return typed artifact obligations and whether workspace-file proof remains required.

    Creative Media artifacts and workspace files are different delivery planes.
    Only a typed Creative contract with ``output.kind=artifact`` may opt a
    write-required brief into governed artifact proof; prose such as
    ``expectedOutputs`` is deliberately not classified here.
    """

    if str(episode.get("kind") or "").strip().lower() != "creative_media":
        return [], True
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    requirements: list[dict[str, Any]] = []
    artifact_brief_keys: set[str] = set()

    def _append_contract(value: Any, *, brief_key: str = "") -> bool:
        if not isinstance(value, Mapping):
            return False
        contract = dict(value)
        schema = str(contract.get("schema") or "").strip()
        if schema not in {"v8.creative_canvas_task.v1", "v8.creative_media_execution.v1"}:
            return False
        output = contract.get("output") if isinstance(contract.get("output"), Mapping) else {}
        output_kind = str(output.get("kind") or "").strip().lower()
        if output_kind not in {"artifact", "artifacts"}:
            return False
        execution = contract.get("execution") if isinstance(contract.get("execution"), Mapping) else {}
        arguments = execution.get("arguments") if isinstance(execution.get("arguments"), Mapping) else {}
        request = arguments.get("request") if isinstance(arguments.get("request"), Mapping) else {}
        if (
            str(execution.get("tool") or "").strip() != "creative_media_jobs"
            or str(arguments.get("action") or "").strip() != "create"
            or not str(request.get("modality") or "").strip()
            or not str(request.get("operationKind") or "").strip()
        ):
            return False
        requirements.append(
            {
                "taskBriefKey": brief_key,
                "request": dict(request),
                "outputKind": output_kind,
                "outputSlot": str(output.get("slot") or "").strip(),
            }
        )
        if brief_key:
            artifact_brief_keys.add(brief_key)
        return True

    for key in ("creativeMediaExecutionContract", "creative_media_execution_contract"):
        if key in inputs:
            _append_contract(inputs.get(key))

    briefs = _episode_task_briefs(episode)
    write_brief_keys: list[str] = []
    for index, brief in enumerate(briefs):
        brief_key = str(brief.get("taskBriefId") or f"brief:{index}").strip()
        if _brief_requires_write(brief, episode_kind="creative_media"):
            write_brief_keys.append(brief_key)
        context = brief.get("context") if isinstance(brief.get("context"), Mapping) else {}
        for key in (
            "creativeMediaExecutionContract",
            "creative_media_execution_contract",
            "canvasExecutionContract",
            "canvas_execution_contract",
        ):
            if key in context:
                _append_contract(context.get(key), brief_key=brief_key)
                break

    if write_brief_keys:
        requires_workspace_file = any(key not in artifact_brief_keys for key in write_brief_keys)
    else:
        requires_workspace_file = not bool(requirements)
    return requirements, requires_workspace_file


def _same_resolved_path(left: str, right: str) -> bool:
    if not str(left or "").strip() or not str(right or "").strip():
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except Exception:
        return False


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _creative_artifact_id(value: Any) -> str:
    if isinstance(value, Mapping):
        text = str(value.get("artifactId") or value.get("artifact_id") or value.get("id") or "").strip()
    else:
        text = str(value or "").strip()
    if text.startswith("artifact://"):
        text = text[len("artifact://") :].strip("/\\")
    return text if re.fullmatch(r"art_[A-Za-z0-9_-]+", text) else ""


def _creative_artifact_evidence(
    episode: Mapping[str, Any],
    handoffs: Iterable[Mapping[str, Any]],
    requirements: list[dict[str, Any]],
) -> tuple[list[str], str | None]:
    """Validate governed Creative artifacts without weakening workspace-file gates."""

    artifact_ids: list[str] = []
    job_proof_ids: set[str] = set()
    delivery_records: list[dict[str, Any]] = []
    for handoff in handoffs:
        payload = _handoff_payload(handoff)
        for value in _collect_named_values(payload, {"artifactRefs", "artifact_refs"}):
            artifact_id = _creative_artifact_id(value)
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
        for value in _collect_named_values(payload, {"proofRefs", "proof_refs", "jobRefs", "job_refs"}):
            text = _ref_text(value) or str(value or "").strip()
            if text.startswith("creative-media-job://"):
                job_id = text[len("creative-media-job://") :].strip("/\\")
                if job_id:
                    job_proof_ids.add(job_id)
        evidence = payload.get("creativeExecutionEvidence")
        if isinstance(evidence, Mapping) and str(evidence.get("schemaVersion") or "") == "creative-execution-evidence/v1":
            for value in list(evidence.get("records") or []):
                if not isinstance(value, Mapping):
                    continue
                record = dict(value)
                job_id = str(record.get("jobId") or "").strip()
                record_artifacts = [
                    _creative_artifact_id(item)
                    for item in list(record.get("artifactRefs") or [])
                ]
                record_artifacts = [item for item in record_artifacts if item]
                if job_id and job_id in job_proof_ids and record_artifacts:
                    delivery_records.append({**record, "artifactRefs": record_artifacts})
    if not artifact_ids:
        return [], "required_creative_artifact_missing"

    episode_session_id = str(episode.get("sessionId") or episode.get("session_id") or "").strip()
    episode_run_id = str(episode.get("runId") or episode.get("run_id") or "").strip()
    if not episode_session_id or not episode_run_id:
        return [], "creative_artifact_lineage_mismatch"
    binding = db.get_session_scope_binding(episode_session_id)
    if not isinstance(binding, Mapping):
        return [], "creative_artifact_lineage_mismatch"
    inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
    expected_workspace_id = str(
        inputs.get("workspaceId")
        or inputs.get("workspace_id")
        or episode.get("workspaceId")
        or episode.get("workspace_id")
        or binding.get("workspace_id")
        or binding.get("workspaceId")
        or ""
    ).strip()
    expected_project_id = str(
        inputs.get("projectId")
        or inputs.get("project_id")
        or episode.get("projectId")
        or episode.get("project_id")
        or binding.get("project_id")
        or binding.get("projectId")
        or ""
    ).strip()
    expected_workspace_path = str(
        inputs.get("workspacePath")
        or inputs.get("workspace_path")
        or binding.get("workspace_path")
        or binding.get("workspacePath")
        or ""
    ).strip()
    bound_workspace_id = str(binding.get("workspace_id") or binding.get("workspaceId") or "").strip()
    bound_project_id = str(binding.get("project_id") or binding.get("projectId") or "").strip()
    bound_workspace_path = str(binding.get("workspace_path") or binding.get("workspacePath") or "").strip()
    if (
        not expected_workspace_id
        or not expected_project_id
        or not expected_workspace_path
        or expected_workspace_id != bound_workspace_id
        or expected_project_id != bound_project_id
        or not _same_resolved_path(expected_workspace_path, bound_workspace_path)
    ):
        return [], "creative_artifact_lineage_mismatch"

    def _source_matches_scope(source_id: str) -> bool:
        source = db.get_session_source(session_id=episode_session_id, source_id=source_id)
        if not isinstance(source, Mapping):
            return False
        resource_ref = source.get("resourceRef") if isinstance(source.get("resourceRef"), Mapping) else {}
        metadata = source.get("metadata") if isinstance(source.get("metadata"), Mapping) else {}
        source_binding = metadata.get("workspaceBinding") if isinstance(metadata.get("workspaceBinding"), Mapping) else {}
        source_workspace_id = str(resource_ref.get("workspaceId") or source_binding.get("workspaceId") or "").strip()
        source_project_id = str(resource_ref.get("projectId") or source_binding.get("projectId") or "").strip()
        source_workspace_root = str(
            resource_ref.get("workspaceRoot")
            or source_binding.get("activeWorkspaceRoot")
            or source_binding.get("authorityWorkspaceRoot")
            or ""
        ).strip()
        return bool(
            source_workspace_id == expected_workspace_id
            and source_project_id == expected_project_id
            and source_workspace_root
            and _same_resolved_path(source_workspace_root, expected_workspace_path)
        )

    for requirement in requirements:
        request = requirement.get("request") if isinstance(requirement.get("request"), Mapping) else {}
        source_ids = [
            str(item).strip()
            for item in [
                request.get("sourceId"),
                *(list(request.get("sourceIds") or []) if isinstance(request.get("sourceIds"), list) else []),
                request.get("maskSourceId"),
            ]
            if str(item or "").strip()
        ]
        if any(not _source_matches_scope(source_id) for source_id in dict.fromkeys(source_ids)):
            return [], "creative_artifact_lineage_mismatch"

    matched_requirements: set[int] = set()
    evidence: list[str] = []
    for artifact_id in artifact_ids:
        artifact = db.get_runtime_artifact(artifact_id)
        if not isinstance(artifact, Mapping):
            return [], "required_creative_artifact_missing"
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
        if str(artifact.get("resourceRole") or artifact.get("resource_role") or "artifact").strip() != "artifact":
            return [], "creative_artifact_lineage_mismatch"
        if (
            str(artifact.get("sessionId") or artifact.get("session_id") or "").strip() != episode_session_id
            or str(artifact.get("runId") or artifact.get("run_id") or "").strip() != episode_run_id
            or str(metadata.get("storageClass") or metadata.get("storage_class") or "").strip() != "runtime_artifact"
            or str(metadata.get("workspaceId") or metadata.get("workspace_id") or "").strip() != expected_workspace_id
            or str(metadata.get("projectId") or metadata.get("project_id") or "").strip() != expected_project_id
            or not _same_resolved_path(
                str(metadata.get("workspacePath") or metadata.get("workspace_path") or "").strip(),
                expected_workspace_path,
            )
        ):
            return [], "creative_artifact_lineage_mismatch"
        source_path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
        try:
            resolved_source_path = Path(source_path).resolve()
        except Exception:
            return [], "required_creative_artifact_missing"
        if (
            not source_path
            or not resolved_source_path.exists()
            or not resolved_source_path.is_file()
            or not _path_is_within(resolved_source_path, Path(expected_workspace_path))
        ):
            return [], "required_creative_artifact_missing"
        job_id = str(metadata.get("creativeMediaJobId") or metadata.get("creative_media_job_id") or "").strip()
        delivery_record = next(
            (
                record
                for record in delivery_records
                if artifact_id in list(record.get("artifactRefs") or [])
            ),
            None,
        )
        if not job_id or (job_id not in job_proof_ids and delivery_record is None):
            return [], "creative_artifact_proof_missing"

        for index, requirement in enumerate(requirements):
            request = requirement.get("request") if isinstance(requirement.get("request"), Mapping) else {}
            lineage_keys = ("modality", "operationKind", "canvasOperationId", "sourceId", "maskSourceId")
            expected_output_kind = str(requirement.get("outputKind") or "").strip()
            record_for_requirement = next(
                (
                    record
                    for record in delivery_records
                    if artifact_id in list(record.get("artifactRefs") or [])
                    and str(record.get("operationKind") or "").strip() == str(request.get("operationKind") or "").strip()
                    and str(record.get("outputKind") or "").strip() == expected_output_kind
                    and str(record.get("outputSlot") or "").strip() == str(requirement.get("outputSlot") or "").strip()
                ),
                None,
            )
            output_kind_matches = (
                str(metadata.get("outputKind") or metadata.get("output_kind") or "").strip() == expected_output_kind
                or record_for_requirement is not None
            )
            expected_output_slot = str(requirement.get("outputSlot") or "").strip()
            output_slot_matches = bool(expected_output_slot) and str(
                metadata.get("outputSlot") or metadata.get("output_slot") or ""
            ).strip() == expected_output_slot
            if record_for_requirement is not None:
                output_slot_matches = True
            metadata_lineage_matches = all(
                not str(request.get(key) or "").strip()
                or str(metadata.get(key) or "").strip() == str(request.get(key) or "").strip()
                for key in lineage_keys
            )
            if output_kind_matches and output_slot_matches and (
                record_for_requirement is not None or metadata_lineage_matches
            ):
                matched_requirements.add(index)
        evidence.append(str(resolved_source_path))

    if len(matched_requirements) != len(requirements):
        return [], "creative_artifact_lineage_mismatch"
    return list(dict.fromkeys(evidence))[:32], None


def _handoff_proof_evidence(handoffs: Iterable[Mapping[str, Any]]) -> list[str]:
    keys = {
        "proofRefs",
        "proof_refs",
        "verificationRefs",
        "verification_refs",
        "evidenceRefs",
        "evidence_refs",
        "verificationResults",
        "verification_results",
    }
    evidence: list[str] = []
    for handoff in handoffs:
        payload = _handoff_payload(handoff)
        for value in _collect_named_values(payload, keys):
            text = _ref_text(value) or str(value or "").strip()
            if text:
                evidence.append(text[:800])
        for verification in _collect_named_values(
            payload,
            {"verification", "verificationResult", "verificationResults", "verification_result", "verification_results"},
        ):
            if not isinstance(verification, Mapping) or not verification:
                continue
            status = str(verification.get("status") or verification.get("state") or "").strip().lower()
            passed = verification.get("passed")
            if passed is True or status in {"passed", "verified", "success", "completed"}:
                evidence.append(f"verification:{status or 'passed'}")
        for acceptance in _collect_named_values(payload, {"acceptanceCheck", "acceptance_check"}):
            if not isinstance(acceptance, Mapping):
                continue
            must = acceptance.get("must") if isinstance(acceptance.get("must"), Mapping) else {}
            if must.get("passed") is True:
                evidence.append("acceptance:must_passed")
    return list(dict.fromkeys(evidence))[:32]


def _non_spec_write_delivery_failure(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    for episode in episodes:
        if str(episode.get("parentEpisodeId") or episode.get("parent_episode_id") or "").strip():
            # Child delegation episodes are implementation details of the owning
            # runtime episode. Their artifacts and proof are merged into the
            # parent's typed handoff; evaluating them again can reject a valid
            # parent delivery merely because the child handoff is intentionally
            # compact.
            continue
        if not _required_write_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        state = str(episode.get("state") or "").strip().lower()
        handoffs = [dict(item) for item in list(handoffs_by_episode.get(episode_id, []) or []) if isinstance(item, Mapping)]
        statuses = {
            str(_handoff_payload(item).get("status") or "").strip().lower()
            for item in handoffs
        }
        if state == "degraded" or statuses.intersection({"degraded", "failed", "blocked", "error"}):
            return {
                "episodeId": episode_id,
                "reason": "required_write_runtime_degraded",
                "state": state,
                "handoffStatuses": sorted(status for status in statuses if status),
                "recoverable": True,
            }
        creative_requirements, requires_workspace_file = _typed_creative_artifact_requirements(episode)
        delivery_evidence: list[str] = []
        if creative_requirements:
            creative_evidence, creative_failure = _creative_artifact_evidence(
                episode,
                handoffs,
                creative_requirements,
            )
            if creative_failure:
                return {
                    "episodeId": episode_id,
                    "reason": creative_failure,
                    "state": state,
                    "recoverable": True,
                }
            delivery_evidence.extend(creative_evidence)
        if requires_workspace_file:
            file_evidence = _existing_file_evidence(episode, handoffs)
            if not file_evidence:
                return {
                    "episodeId": episode_id,
                    "reason": "required_write_files_missing",
                    "state": state,
                    "recoverable": True,
                }
            delivery_evidence.extend(file_evidence)
        proof_evidence = _handoff_proof_evidence(handoffs)
        if not proof_evidence:
            return {
                "episodeId": episode_id,
                "reason": "required_write_proof_missing",
                "state": state,
                "deliveryEvidence": delivery_evidence[:8],
                "recoverable": True,
            }
    return None


def _unresolved_research_evidence_gaps(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Return latest unresolved Research brief truth across bounded retries."""

    latest: dict[str, tuple[str, int, dict[str, Any]]] = {}
    sequence = 0
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        if str(episode.get("kind") or "").strip().lower() != "research":
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        for raw_handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            if not isinstance(raw_handoff, Mapping):
                continue
            sequence += 1
            handoff = _handoff_payload(raw_handoff)
            kind = str(handoff.get("kind") or "").strip().lower()
            if "research" not in kind:
                continue
            timestamp = str(
                handoff.get("createdAt")
                or raw_handoff.get("created_at")
                or episode.get("updatedAt")
                or episode.get("updated_at")
                or episode.get("createdAt")
                or episode.get("created_at")
                or ""
            )
            results = [dict(item) for item in list(handoff.get("taskBriefResults") or []) if isinstance(item, Mapping)]
            covered_ids = [str(item).strip() for item in list(handoff.get("coveredTaskBriefIds") or []) if str(item).strip()]
            missing_ids = [str(item).strip() for item in list(handoff.get("missingTaskBriefIds") or []) if str(item).strip()]

            def _record(brief_id: str, *, status: str, reasons: list[str] | None = None) -> None:
                if not brief_id:
                    return
                record = {
                    "episodeId": episode_id,
                    "handoffRefId": handoff.get("handoffRefId") or handoff.get("handoffId"),
                    "taskBriefId": brief_id,
                    "status": status,
                    "evidenceStatusReasons": list(reasons or [])[:8],
                }
                previous = latest.get(brief_id)
                key = (timestamp, sequence)
                if previous is None or key >= (previous[0], previous[1]):
                    latest[brief_id] = (timestamp, sequence, record)

            for result in results:
                brief_id = str(result.get("taskBriefId") or result.get("taskId") or "").strip()
                status = str(result.get("status") or "degraded").strip().lower()
                _record(
                    brief_id,
                    status=status,
                    reasons=[str(item) for item in list(result.get("evidenceStatusReasons") or []) if str(item).strip()],
                )
            for brief_id in covered_ids:
                _record(brief_id, status="ready")
            for brief_id in missing_ids:
                _record(brief_id, status="degraded", reasons=["missing_task_brief_evidence"])
            if str(handoff.get("status") or "").strip().lower() == "degraded" and not (results or covered_ids or missing_ids):
                fallback_ids = [
                    str(item).strip()
                    for item in list(handoff.get("taskBriefIds") or [])
                    if str(item).strip()
                ] or [f"research:{episode_id or 'unknown'}"]
                for brief_id in fallback_ids:
                    _record(brief_id, status="degraded", reasons=["research_handoff_degraded"])

    return [
        record
        for _timestamp, _sequence, record in latest.values()
        if str(record.get("status") or "").strip().lower() not in {"ready", "completed", "success", "ok"}
    ]


def _completed_downstream_carrying_research_gaps(
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
    research_gaps: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return governed downstream evidence that explicitly carried gaps.

    Missing external evidence remains a claim blocker.  It may stop being a
    whole-run blocker only after a downstream runtime received the exact gap
    IDs and returned a ready handoff with its own local proof contract.
    """

    missing_ids = {
        str(item.get("taskBriefId") or "").strip()
        for item in research_gaps
        if str(item.get("taskBriefId") or "").strip()
    }
    if not missing_ids:
        return None
    downstream_kinds = {"engineering", "creative_media", "computer_use", "rpa", "delegation"}
    for episode in episodes:
        if _is_optional_episode(episode):
            continue
        kind = str(episode.get("kind") or "").strip().lower()
        if kind not in downstream_kinds:
            continue
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), Mapping) else {}
        context = inputs.get("researchContext") if isinstance(inputs.get("researchContext"), Mapping) else {}
        carried_gaps = {
            str(item.get("taskBriefId") or item.get("taskId") or "").strip()
            for item in list(context.get("evidenceGaps") or [])
            if isinstance(item, Mapping)
            and str(item.get("taskBriefId") or item.get("taskId") or "").strip()
        }
        if not missing_ids.issubset(carried_gaps) or not bool(context.get("downstreamAllowed")):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        ready_handoffs: list[dict[str, Any]] = []
        for raw_handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            if not isinstance(raw_handoff, Mapping):
                continue
            handoff = _handoff_payload(raw_handoff)
            if str(handoff.get("status") or "").strip().lower() not in {"ready", "completed", "success", "ok"}:
                continue
            ready_handoffs.append(
                {
                    "handoffRefId": handoff.get("handoffRefId") or handoff.get("handoffId"),
                    "proofRefs": list(handoff.get("proofRefs") or handoff.get("verificationRefs") or [])[:8],
                    "artifactRefs": list(handoff.get("artifactRefs") or handoff.get("refs") or [])[:8],
                }
            )
        if ready_handoffs:
            return {
                "episodeId": episode_id,
                "kind": kind,
                "carriedTaskBriefIds": sorted(carried_gaps),
                "handoffs": ready_handoffs[:4],
            }
    return None


def _explicit_research_transport_blocker_report(
    *,
    final_text: str,
    research_gaps: Iterable[Mapping[str, Any]],
    episodes: Iterable[Mapping[str, Any]],
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]],
) -> dict[str, Any] | None:
    """Allow a truthful transport blocker to be delivered as the answer.

    This is deliberately narrower than Research acceptance.  It never turns
    missing evidence into an accepted fact: every unresolved brief must have
    a typed, exhausted source-acquisition diagnostic, and the Supervisor's
    final text must explicitly say that no verified answer can be given and
    tell the user how to repair/retry it.  Without that acknowledgement the
    normal fail-closed evidence gate remains in force.
    """

    normalized_text = str(final_text or "").strip().lower()
    if not normalized_text:
        return None
    acknowledgement_markers = (
        "无可读证据",
        "没有可读证据",
        "无法验证",
        "无法完成调研",
        "研究阻塞",
        "调研阻塞",
        "no readable evidence",
        "no verified answer",
        "research is blocked",
        "research blocker",
    )
    repair_markers = (
        "重新调研",
        "修复",
        "登录态",
        "allowlist",
        "配置",
        "重试",
        "retry",
        "repair",
        "configure",
        "重新运行",
    )
    if not any(marker in normalized_text for marker in acknowledgement_markers):
        return None
    if not any(marker in normalized_text for marker in repair_markers):
        return None

    def bounded_count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    missing_ids = {
        str(item.get("taskBriefId") or "").strip()
        for item in research_gaps
        if str(item.get("taskBriefId") or "").strip()
    }
    if not missing_ids:
        return None

    exhausted_ids: set[str] = set()
    exhausted_episode_ids: set[str] = set()
    for episode in episodes:
        if str(episode.get("kind") or "").strip().lower() != "research":
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if not episode_id:
            continue
        for raw_handoff in list(handoffs_by_episode.get(episode_id, []) or []):
            if not isinstance(raw_handoff, Mapping):
                continue
            handoff = _handoff_payload(raw_handoff)
            if str(handoff.get("status") or "").strip().lower() != "degraded":
                continue
            acquisition = handoff.get("sourceAcquisition")
            acquisition = acquisition if isinstance(acquisition, Mapping) else {}
            by_brief = {
                str(item.get("taskBriefId") or "").strip(): item
                for item in list(handoff.get("sourceAcquisitionByBrief") or [])
                if isinstance(item, Mapping) and str(item.get("taskBriefId") or "").strip()
            }
            results = [item for item in list(handoff.get("taskBriefResults") or []) if isinstance(item, Mapping)]
            for result in results:
                brief_id = str(result.get("taskBriefId") or "").strip()
                if not brief_id:
                    continue
                result_acquisition = result.get("sourceAcquisition")
                result_acquisition = result_acquisition if isinstance(result_acquisition, Mapping) else by_brief.get(brief_id, {})
                if (
                    str(result_acquisition.get("state") or acquisition.get("state") or "").strip().lower() == "exhausted"
                    and bounded_count(result_acquisition.get("readableSourceCount") or acquisition.get("readableSourceCount")) == 0
                    and bounded_count(result_acquisition.get("selectedSourceCount") or acquisition.get("selectedSourceCount")) == 0
                ):
                    exhausted_ids.add(brief_id)
                    exhausted_episode_ids.add(episode_id)
            # Older handoffs may omit taskBriefResults.  In that case the
            # episode-level acquisition diagnostic still binds the gap.
            if not results and str(acquisition.get("state") or "").strip().lower() == "exhausted":
                handoff_missing_ids = {
                    str(item.get("taskBriefId") if isinstance(item, Mapping) else item or "").strip()
                    for item in list(handoff.get("missingTaskBriefIds") or [])
                    if str(item.get("taskBriefId") if isinstance(item, Mapping) else item or "").strip()
                }
                exhausted_ids.update(
                    missing_id
                    for missing_id in missing_ids
                    if not handoff_missing_ids or missing_id in handoff_missing_ids
                )
                exhausted_episode_ids.add(episode_id)

    if not missing_ids.issubset(exhausted_ids) or not exhausted_episode_ids:
        return None
    return {
        "episodeIds": sorted(exhausted_episode_ids)[:12],
        "missingTaskBriefIds": sorted(missing_ids)[:12],
        "delivery": "truthful_transport_blocker_report",
    }


def evaluate_supervisor_completion(
    *,
    episodes: Iterable[Mapping[str, Any]] = (),
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    final_text: str = "",
    spec_mode: bool = False,
    spec_brief: Mapping[str, Any] | None = None,
    spec_has_pending_approval: bool | None = None,
) -> SupervisorCompletionDecision:
    normalized_episodes = [dict(item) for item in episodes if isinstance(item, Mapping)]
    normalized_handoffs = {
        str(episode_id): [dict(item) for item in items if isinstance(item, Mapping)]
        for episode_id, items in dict(handoffs_by_episode or {}).items()
    }

    active = [
        str(item.get("episodeId") or item.get("id") or "")
        for item in normalized_episodes
        if str(item.get("state") or "").strip().lower() in ACTIVE_EPISODE_STATES
    ]
    if active:
        return SupervisorCompletionDecision(
            action="waiting_runtime",
            reason="runtime_episode_active_at_stream_end",
            details={"episodeIds": active[:12]},
        )

    pseudo_tools = _pseudo_side_effect_tool_names(final_text)
    if pseudo_tools:
        return SupervisorCompletionDecision(
            action="fail",
            reason="supervisor_pseudo_tool_markup_not_executed",
            details={
                "toolNames": pseudo_tools,
                "nextAction": "retry_with_native_structured_tool_calls_or_report_blocker",
            },
        )

    superseded_ids = superseded_runtime_episode_ids(normalized_episodes, normalized_handoffs)
    effective_episodes = [
        episode
        for episode in normalized_episodes
        if not runtime_episode_parent_id(episode)
        and str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
        not in superseded_ids
    ]

    current_handoffs, delivery_integrity_failure = _current_runtime_handoffs(
        effective_episodes,
        normalized_handoffs,
    )
    if delivery_integrity_failure:
        reason = str(delivery_integrity_failure.get("reason") or "runtime_result_handoff_missing")
        return SupervisorCompletionDecision(
            action=(
                "waiting_runtime"
                if reason in {"required_runtime_handoff_missing", "runtime_result_handoff_missing"}
                else "fail"
            ),
            reason=reason,
            details={
                key: value
                for key, value in delivery_integrity_failure.items()
                if key != "reason"
            },
        )

    for episode in effective_episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "")
        episode_kind = str(episode.get("kind") or "").strip().lower()
        state = str(episode.get("state") or "").strip().lower()
        handoffs = current_handoffs.get(episode_id, [])
        if state in {"failed", "cancelled"} and not any(
            str(_handoff_payload(item).get("status") or "").strip().lower() in {"ready", "degraded"}
            for item in handoffs
        ):
            return SupervisorCompletionDecision(
                action="fail",
                reason="required_runtime_episode_failed_without_handoff",
                details={"episodeId": episode_id, "state": state},
            )
        for raw_handoff in handoffs:
            handoff = _handoff_payload(raw_handoff)
            status = str(handoff.get("status") or "").strip().lower()
            kind = str(handoff.get("kind") or "").strip().lower()
            run_mode = str(handoff.get("runMode") or "").strip().lower()
            if kind == "research_evidence_bundle" and status == "ready" and run_mode == "plan":
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="research_plan_only_claimed_evidence_ready",
                    details={"episodeId": episode_id, "handoffRefId": handoff.get("handoffRefId")},
                )
            if status in {"failed", "blocked"}:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="required_runtime_handoff_failed",
                    details={
                        "episodeId": episode_id,
                        "handoffRefId": handoff.get("handoffRefId"),
                        "status": status,
                    },
                )
            nested_failures = _required_nested_delegation_failures(
                handoff,
                episode_kind=episode_kind,
            )
            if nested_failures:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="required_delegation_result_failed",
                    details={
                        "episodeId": episode_id,
                        "handoffRefId": handoff.get("handoffRefId") or handoff.get("handoffId"),
                        "failedResultCount": len(nested_failures),
                        "failedTaskBriefIds": list(
                            dict.fromkeys(
                                str(item.get("taskBriefId") or "").strip()
                                for item in nested_failures
                                if str(item.get("taskBriefId") or "").strip()
                            )
                        )[:24],
                        "failures": nested_failures[:24],
                        "nextAction": "repair_or_retry_only_the_required_failed_delegation_results",
                    },
                )

    research_gaps = _unresolved_research_evidence_gaps(effective_episodes, current_handoffs)
    research_gap_continuation = None
    if research_gaps:
        research_gap_continuation = _completed_downstream_carrying_research_gaps(
            effective_episodes,
            current_handoffs,
            research_gaps,
        )
        if research_gap_continuation is None:
            transport_blocker = _explicit_research_transport_blocker_report(
                final_text=final_text,
                research_gaps=research_gaps,
                episodes=effective_episodes,
                handoffs_by_episode=current_handoffs,
            )
            if transport_blocker is not None:
                return SupervisorCompletionDecision(
                    action="complete",
                    reason="research_transport_blocker_reported",
                    details=transport_blocker,
                )
            return SupervisorCompletionDecision(
                action="fail",
                reason="research_brief_evidence_incomplete",
                details={
                    "missingTaskBriefIds": [str(item.get("taskBriefId") or "") for item in research_gaps[:12]],
                    "gaps": research_gaps[:12],
                    "nextAction": "retry_missing_research_briefs_once_or_continue_with_explicit_gaps",
                },
            )

    missing_delegation_acceptance = _delegation_acceptance_missing(
        effective_episodes,
        current_handoffs,
        final_text=final_text,
    )
    if missing_delegation_acceptance:
        return SupervisorCompletionDecision(
            action="fail",
            reason="delegation_supervisor_acceptance_missing",
            details={
                "episodeIds": missing_delegation_acceptance[:12],
                "nextAction": "record_accept_retry_or_ignore",
            },
        )

    if not spec_mode:
        write_delivery_failure = _non_spec_write_delivery_failure(effective_episodes, current_handoffs)
        if write_delivery_failure:
            return SupervisorCompletionDecision(
                action="fail",
                reason=str(write_delivery_failure.get("reason") or "required_write_delivery_incomplete"),
                details={
                    **write_delivery_failure,
                    "nextAction": "repair_or_retry_required_write_episode",
                },
            )

    if spec_mode:
        brief = dict(spec_brief or {})
        if not str(brief.get("specId") or "").strip() or str(brief.get("status") or "").strip().lower() in {
            "missing",
            "error",
        }:
            return SupervisorCompletionDecision(action="fail", reason="spec_stage_not_created")
        pipeline = brief.get("pipelineControl") if isinstance(brief.get("pipelineControl"), Mapping) else {}
        blocked_reason = str(pipeline.get("blockedReason") or "").strip()
        blocked_stage = str(pipeline.get("blockedByApproval") or "").strip()
        if blocked_reason in {"stage_format_invalid", "stage_analysis_invalid", "stage_contract_invalid"}:
            return SupervisorCompletionDecision(
                action="fail",
                reason=f"spec_{blocked_reason}",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "blockedReason": blocked_reason,
                },
            )
        if blocked_stage or blocked_reason == "approval_required":
            if spec_has_pending_approval is False:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_stage_blocked_without_pending_approval",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        "blockedByApproval": blocked_stage,
                    },
                )
            return SupervisorCompletionDecision(
                action="waiting_approval",
                reason=blocked_reason or "approval_required",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "blockedByApproval": blocked_stage,
                },
            )
        if bool(pipeline.get("runtimeExecutionAllowed")) and not _has_ready_runtime_handoff(
            effective_episodes,
            current_handoffs,
        ):
            if not effective_episodes:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_runtime_execution_episode_missing",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        "episodeCount": 0,
                    },
                )
            return SupervisorCompletionDecision(
                action="waiting_runtime",
                reason="spec_runtime_execution_handoff_pending",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "episodeCount": len(effective_episodes),
                },
            )
        if bool(pipeline.get("runtimeExecutionAllowed")):
            degraded_handoffs = _required_runtime_degraded_handoffs(effective_episodes, current_handoffs)
            if degraded_handoffs:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_runtime_execution_degraded",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        "handoffs": degraded_handoffs[:8],
                    },
                )
            missing_proof = _missing_spec_proof_handoffs(brief, effective_episodes, current_handoffs)
            if missing_proof:
                return SupervisorCompletionDecision(
                    action="fail",
                    reason="spec_runtime_execution_proof_missing",
                    details={
                        "specId": brief.get("specId"),
                        "currentStage": brief.get("currentStage"),
                        **missing_proof,
                    },
                )
        # A fast client-side approval can be applied before the turn that wrote
        # the previous stage reaches finalization. In that race window the
        # pipeline legitimately has `nextStage=design|tasks` and no approval
        # block yet; the command router will schedule the continuation run.
        # Treating this as a failure poisons the run with a false terminal
        # status while the continuation is already in flight.

    if research_gap_continuation is None and effective_episodes and _looks_forward_only(final_text):
        return SupervisorCompletionDecision(
            action="complete",
            reason="forward_only_supervisor_advisory",
            details={
                "severity": "advisory",
                "finalTextPreview": str(final_text or "").strip()[:240],
                "message": "Supervisor ended with forward-looking wording; review delivery completeness without overriding its decision.",
            },
        )

    if research_gap_continuation is not None:
        return SupervisorCompletionDecision(
            action="complete",
            reason="research_gaps_carried_to_verified_downstream",
            details={
                "severity": "advisory",
                "missingTaskBriefIds": [str(item.get("taskBriefId") or "") for item in research_gaps[:12]],
                "gaps": research_gaps[:12],
                "downstream": research_gap_continuation,
                "message": (
                    "Unverified Research claims remain omitted, while a downstream runtime carried the exact gap IDs "
                    "and returned governed local delivery evidence."
                ),
            },
        )

    return SupervisorCompletionDecision()


__all__ = ["SupervisorCompletionDecision", "evaluate_supervisor_completion"]
