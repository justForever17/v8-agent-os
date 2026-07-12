from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from core.runtime_episodes import ACTIVE_EPISODE_STATES


RUNTIME_EXECUTION_HANDOFF_STATUSES = {"ready", "degraded"}


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
        if blocked_reason == "stage_format_invalid":
            return SupervisorCompletionDecision(
                action="fail",
                reason="spec_stage_format_invalid",
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
