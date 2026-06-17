from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


ACTIVE_EPISODE_STATES = {
    "detected",
    "routed",
    "queued",
    "leased",
    "active",
    "waiting",
    "waiting_child",
    "waiting_external",
    "waiting_approval",
}


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


def evaluate_supervisor_completion(
    *,
    episodes: Iterable[Mapping[str, Any]] = (),
    handoffs_by_episode: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    final_text: str = "",
    spec_mode: bool = False,
    spec_brief: Mapping[str, Any] | None = None,
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
            action="fail",
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
            return SupervisorCompletionDecision(
                action="waiting_approval",
                reason=blocked_reason or "approval_required",
                details={
                    "specId": brief.get("specId"),
                    "currentStage": brief.get("currentStage"),
                    "blockedByApproval": blocked_stage,
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
            action="fail",
            reason="forward_only_supervisor_final_text",
            details={"finalTextPreview": str(final_text or "").strip()[:240]},
        )

    return SupervisorCompletionDecision()


__all__ = ["SupervisorCompletionDecision", "evaluate_supervisor_completion"]
