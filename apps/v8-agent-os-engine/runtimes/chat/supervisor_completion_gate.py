from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from core.database import db
from core.runtime_episodes import ACTIVE_EPISODE_STATES


RUNTIME_EXECUTION_HANDOFF_STATUSES = {"ready", "degraded"}
DELEGATION_ACCEPTANCE_DECISION_RE = re.compile(
    r"(?:验收决定|acceptance\s+decision)\s*[：:]\s*[`*_~]*\s*(ACCEPT|RETRY|IGNORE)\b\s*[`*_~]*",
    re.IGNORECASE,
)


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
    if DELEGATION_ACCEPTANCE_DECISION_RE.search(str(final_text or "")):
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
    workspace = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip()
    workspace_path = Path(workspace).resolve() if workspace else None
    values: list[Any] = []
    keys = {
        "artifactRefs",
        "artifacts",
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
            if workspace_path is None:
                continue
            candidate_text = text[len("workspace://") :].lstrip("/\\")
        elif text.startswith("file://"):
            candidate_text = text[7:]
        try:
            candidate = Path(candidate_text)
            if not candidate.is_absolute() and workspace_path is not None:
                candidate = workspace_path / candidate
            if candidate.exists() and candidate.is_file():
                evidence.append(str(candidate.resolve()))
        except Exception:
            continue
    return list(dict.fromkeys(evidence))[:32]


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
        file_evidence = _existing_file_evidence(episode, handoffs)
        if not file_evidence:
            return {
                "episodeId": episode_id,
                "reason": "required_write_files_missing",
                "state": state,
                "recoverable": True,
            }
        proof_evidence = _handoff_proof_evidence(handoffs)
        if not proof_evidence:
            return {
                "episodeId": episode_id,
                "reason": "required_write_proof_missing",
                "state": state,
                "fileEvidence": file_evidence[:8],
                "recoverable": True,
            }
    return None


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

    for episode in normalized_episodes:
        if _is_optional_episode(episode):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "")
        state = str(episode.get("state") or "").strip().lower()
        handoffs = normalized_handoffs.get(episode_id, [])
        if state in {"failed", "cancelled"} and not any(
            str(item.get("status") or "").strip().lower() in {"ready", "degraded"} for item in handoffs
        ):
            return SupervisorCompletionDecision(
                action="fail",
                reason="required_runtime_episode_failed_without_handoff",
                details={"episodeId": episode_id, "state": state},
            )
        for handoff in handoffs:
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

    missing_delegation_acceptance = _delegation_acceptance_missing(
        normalized_episodes,
        normalized_handoffs,
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
        write_delivery_failure = _non_spec_write_delivery_failure(normalized_episodes, normalized_handoffs)
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
            normalized_episodes,
            normalized_handoffs,
        ):
            if not normalized_episodes:
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
                    "episodeCount": len(normalized_episodes),
                },
            )
        if bool(pipeline.get("runtimeExecutionAllowed")):
            degraded_handoffs = _required_runtime_degraded_handoffs(normalized_episodes, normalized_handoffs)
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
            missing_proof = _missing_spec_proof_handoffs(brief, normalized_episodes, normalized_handoffs)
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

    if normalized_episodes and _looks_forward_only(final_text):
        return SupervisorCompletionDecision(
            action="complete",
            reason="forward_only_supervisor_advisory",
            details={
                "severity": "advisory",
                "finalTextPreview": str(final_text or "").strip()[:240],
                "message": "Supervisor ended with forward-looking wording; review delivery completeness without overriding its decision.",
            },
        )

    return SupervisorCompletionDecision()


__all__ = ["SupervisorCompletionDecision", "evaluate_supervisor_completion"]
