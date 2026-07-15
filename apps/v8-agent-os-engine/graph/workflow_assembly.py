import asyncio
import json
import os
import time
from datetime import datetime, timezone

from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from core.database import db
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.runtime_episodes import (
    ACTIVE_EPISODE_STATES,
    TERMINAL_EPISODE_STATES,
    append_handoff_ref,
    emit_runtime_episode_event,
    transition_runtime_episode,
)
from core.time_truth import utc_now_iso
from erc.runtime_context import get_runtime_context
from erc.runtime_stability import runtime_stability_service
from .parallel_support import build_parallel_delegate_join_node, build_parallel_delegate_task_node


RUNTIME_EPISODE_WAIT_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_WAIT_SECONDS", "600"))
RUNTIME_EPISODE_QUEUE_GRACE_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_QUEUE_GRACE_SECONDS", "60"))
RUNTIME_EPISODE_POLL_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_POLL_SECONDS", "0.8"))


def _merged_tool_command_update(commands: list[Command]) -> dict:
    """Merge ToolNode Command updates for one deterministic runtime transition."""
    merged: dict = {}
    messages: list = []
    for item in commands:
        update = dict(getattr(item, "update", None) or {})
        for key, value in update.items():
            if key == "messages":
                messages.extend(list(value or []))
            elif isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**dict(merged[key]), **value}
            else:
                merged[key] = value
    if messages:
        merged["messages"] = messages
    return merged


def _route_runtime_tool_commands(command):
    """Convert ToolNode ``Command[]`` runtime routes into graph-owned waits.

    LangGraph returns a list whenever any executed tool returns ``Command``.
    Treating that list as opaque left the queued episode stranded at the
    Supervisor node.  We preserve ordinary multi-command routing unchanged and
    collapse only the explicit runtime wait transition.
    """
    command_items = [item for item in command if isinstance(item, Command)] if isinstance(command, list) else []
    update = _merged_tool_command_update(command_items) if command_items else dict(getattr(command, "update", None) or {})
    runtime_status = dict(update.get("runtime_dispatch_status") or {})
    messages = list(update.get("messages") or [])
    should_wait = str(runtime_status.get("nextAction") or "").strip() == "wait_episode"
    for message in messages:
        additional = dict(getattr(message, "additional_kwargs", None) or {})
        if str(additional.get("recommendedNextAction") or "").strip() == "wait_episode":
            should_wait = True
            break
    if should_wait:
        return Command(goto="runtime_episode", update=update)
    return command


def _workflow_entry_command(state):
    runtime_status = dict((state or {}).get("runtime_dispatch_status") or {})
    if str(runtime_status.get("nextAction") or "").strip() == "wait_episode":
        return Command(goto="runtime_episode")
    return Command(goto="supervisor")


def _string_value(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _state_runtime_identity(state: dict | None) -> tuple[str | None, str | None, str | None]:
    runtime_context = get_runtime_context()
    state_dict = dict(state or {})
    route_context = dict(state_dict.get("current_route_context") or {})
    session_id = _string_value(
        state_dict.get("session_id"),
        state_dict.get("sessionId"),
        route_context.get("session_id"),
        route_context.get("sessionId"),
        runtime_context.get("session_id"),
        runtime_context.get("sessionId"),
    ) or None
    run_id = _string_value(
        state_dict.get("run_id"),
        state_dict.get("runId"),
        route_context.get("run_id"),
        route_context.get("runId"),
        runtime_context.get("run_id"),
        runtime_context.get("runId"),
    ) or None
    workspace_path = _string_value(
        state_dict.get("workspace_path"),
        state_dict.get("workspacePath"),
        route_context.get("workspace_path"),
        route_context.get("workspacePath"),
        runtime_context.get("workspace_path"),
        runtime_context.get("workspacePath"),
    ) or None
    return session_id, run_id, workspace_path


def _has_live_bound_episode_lease() -> bool:
    """Return true when a runner is actively working a canonical session-bound episode."""
    now_iso = utc_now_iso()
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runtime_episode_queue
                WHERE state = 'leased'
                  AND COALESCE(session_id, '') <> ''
                  AND COALESCE(run_id, '') <> ''
                  AND COALESCE(lease_expires_at, '') > ?
                LIMIT 1
                """,
                (now_iso,),
            ).fetchone()
            return bool(row)
    except Exception:
        return False


def build_runtime_episode_wait_node():
    def _route_context_episode_ids(route_context: dict) -> list[str]:
        ids: list[str] = []
        for item in list(route_context.get("capabilityEpisodes") or []):
            if not isinstance(item, dict):
                continue
            episode_id = _string_value(item.get("episodeId"), item.get("needId"), item.get("id"))
            state = str(item.get("state") or "").strip()
            if episode_id and state in ACTIVE_EPISODE_STATES and episode_id not in ids:
                ids.append(episode_id)
        return ids

    def _load_relevant_episodes(*, route_context: dict, session_id: str | None, run_id: str | None) -> list[dict]:
        by_id: dict[str, dict] = {}
        for episode_id in _route_context_episode_ids(route_context):
            try:
                episode = db.get_runtime_episode(episode_id)
            except Exception:
                episode = None
            if episode:
                route_episode = {}
                for item in list(route_context.get("capabilityEpisodes") or []):
                    if _string_value(item.get("episodeId"), item.get("needId"), item.get("id")) == episode_id:
                        route_episode = dict(item)
                        break
                by_id[str(episode.get("episodeId") or episode.get("id") or episode_id)] = {**route_episode, **dict(episode)}
            else:
                for item in list(route_context.get("capabilityEpisodes") or []):
                    if _string_value(item.get("episodeId"), item.get("needId"), item.get("id")) == episode_id:
                        by_id[episode_id] = dict(item)
                        break
        try:
            db_rows = db.list_runtime_episodes(run_id=run_id, limit=100) if run_id else []
            if not db_rows and session_id:
                db_rows = db.list_runtime_episodes(session_id=session_id, limit=100)
        except Exception:
            db_rows = []
        for episode in db_rows:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if episode_id:
                by_id[episode_id] = {**dict(by_id.get(episode_id) or {}), **dict(episode)}
        return list(by_id.values())

    def _active_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip() in ACTIVE_EPISODE_STATES
        ]

    def _terminal_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip() in TERMINAL_EPISODE_STATES
        ]

    def _episode_queue_age_seconds(episode: dict, *, default_started_wall: float) -> float:
        raw_value = _string_value(
            episode.get("updatedAt"),
            episode.get("updated_at"),
            episode.get("createdAt"),
            episode.get("created_at"),
        )
        if raw_value:
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, time.time() - parsed.timestamp())
            except Exception:
                pass
        return max(0.0, time.time() - default_started_wall)

    def _merge_handoffs(route_context: dict, episodes: list[dict]) -> tuple[dict, list[dict]]:
        updated = dict(route_context or {})
        existing_ids = {
            str(item.get("handoffRefId") or item.get("handoffId") or item.get("artifactId") or "").strip()
            for item in list(updated.get("handoffRefs") or [])
            if isinstance(item, dict)
        }
        merged: list[dict] = []
        for episode in episodes:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if not episode_id:
                continue
            try:
                handoffs = db.list_runtime_episode_handoffs(episode_id)
            except Exception:
                handoffs = []
            for row in handoffs:
                payload = dict(row.get("payload") or row.get("handoff") or row)
                handoff_id = _string_value(payload.get("handoffRefId"), payload.get("handoffId"), payload.get("artifactId"))
                if handoff_id and handoff_id in existing_ids:
                    continue
                updated = append_handoff_ref(updated, payload)
                if handoff_id:
                    existing_ids.add(handoff_id)
                merged.append(payload)
        return updated, merged

    def _compact_handoff_projection(handoff: dict) -> dict:
        results = [item for item in list(handoff.get("results") or []) if isinstance(item, dict)]
        compact_results: list[dict] = []
        for item in results[:8]:
            compact_results.append(
                {
                    "taskBriefId": _string_value(item.get("taskBriefId"), item.get("taskId")),
                    "targetLabel": _string_value(item.get("targetLabel"), item.get("agentName"), item.get("agentId")),
                    "status": _string_value(item.get("status")),
                    "result": _string_value(item.get("resultText"), item.get("summary"), item.get("localSelfCheck"))[:1200],
                    "artifactRefs": list(item.get("artifactRefs") or item.get("artifacts") or [])[:8],
                    "proofRefs": list(item.get("proofRefs") or [])[:8],
                    "blockers": list(item.get("blockers") or item.get("residualRisks") or [])[:6],
                }
            )
        return {
            "handoffRefId": _string_value(handoff.get("handoffRefId"), handoff.get("handoffId")),
            "producerEpisodeId": _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId")),
            "kind": _string_value(handoff.get("kind"), "runtime_handoff"),
            "status": _string_value(handoff.get("status")),
            "summary": _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:1200],
            "refs": list(handoff.get("refs") or handoff.get("artifactRefs") or [])[:10],
            "proofRefs": list(handoff.get("proofRefs") or handoff.get("verificationRefs") or [])[:10],
            "results": compact_results,
            "consumerHint": _string_value(handoff.get("consumerHint"), handoff.get("recommendedNextAction"))[:600],
        }

    def _summary_message(*, episodes: list[dict], handoffs: list[dict], status: str, reason: str = "") -> HumanMessage:
        lines = [f"[Runtime Episode {status}]"]
        compact_handoffs = [_compact_handoff_projection(handoff) for handoff in handoffs[:8]]
        if reason:
            lines.append(f"Reason: {reason}")
        if compact_handoffs:
            lines.append("Typed handoffs:")
            for handoff in compact_handoffs:
                kind = _string_value(handoff.get("kind"), "runtime_handoff")
                summary = _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:800]
                status_label = _string_value(handoff.get("status"))
                lines.append(f"- {kind}{f' / {status_label}' if status_label else ''}: {summary}")
                for result in list(handoff.get("results") or [])[:4]:
                    task_id = _string_value(result.get("taskBriefId"), "task")
                    target = _string_value(result.get("targetLabel"), "worker")
                    result_status = _string_value(result.get("status"), "unknown")
                    result_text = _string_value(result.get("result"))[:500]
                    lines.append(f"  - {task_id} · {target} · {result_status}: {result_text or '已回传结构化结果。'}")
                    artifact_count = len(list(result.get("artifactRefs") or []))
                    proof_count = len(list(result.get("proofRefs") or []))
                    if artifact_count or proof_count:
                        lines.append(f"    evidence: artifacts={artifact_count}, proofRefs={proof_count}")
        else:
            lines.append("Episodes:")
            for episode in episodes[:8]:
                lines.append(
                    "- "
                    f"{_string_value(episode.get('kind'), 'runtime')} "
                    f"{_string_value(episode.get('episodeId'), episode.get('id'), episode.get('needId'))} "
                    f"state={_string_value(episode.get('state'))}"
                )
        lines.append("Supervisor must use these runtime facts and must not retry direct mutating tools while active episodes remain.")
        return HumanMessage(
            content="\n".join(lines),
            additional_kwargs={
                "v8_governance_type": "runtime_handoff",
                "v8_runtime_handoffs": compact_handoffs,
            },
        )

    def _failed_handoffs(handoffs: list[dict]) -> list[dict]:
        return [
            handoff
            for handoff in handoffs
            if str(handoff.get("status") or "").strip().lower() in {"failed", "blocked", "error", "recoverable_failed"}
        ]

    def _degraded_handoffs(handoffs: list[dict]) -> list[dict]:
        degraded: list[dict] = []
        for handoff in handoffs:
            status = str(handoff.get("status") or "").strip().lower()
            kind = str(handoff.get("kind") or "").strip().lower()
            dispatch_status = str(handoff.get("dispatchStatus") or handoff.get("dispatch_status") or "").strip().lower()
            if (
                status == "degraded"
                or kind.endswith("_degraded")
                or bool(handoff.get("degraded") or handoff.get("degradedReason") or handoff.get("degraded_reason"))
                or dispatch_status in {"delegation_degraded", "missing_tasks"}
            ):
                degraded.append(handoff)
        return degraded

    def _failure_summary_key(
        *,
        episodes: list[dict],
        handoffs: list[dict],
        reason: str,
    ) -> str:
        episode_id = ""
        if episodes:
            episode_id = _string_value(
                episodes[0].get("episodeId"),
                episodes[0].get("id"),
                episodes[0].get("needId"),
            )
        if not episode_id and handoffs:
            episode_id = _string_value(
                handoffs[0].get("producerEpisodeId"),
                handoffs[0].get("episodeId"),
            )
        return f"{episode_id or 'runtime'}:{reason or 'failure'}"

    def _is_optional_episode(episode: dict) -> bool:
        inputs = dict(episode.get("inputs") or {}) if isinstance(episode.get("inputs"), dict) else {}
        metadata = dict(episode.get("metadata") or {}) if isinstance(episode.get("metadata"), dict) else {}
        if any(
            bool(source.get("optional") or source.get("optionalLane") or source.get("degradedOk"))
            for source in (episode, inputs, metadata)
            if isinstance(source, dict)
        ):
            return True
        return str(inputs.get("dependencyMode") or metadata.get("dependencyMode") or episode.get("dependencyMode") or "").strip().lower() in {
            "optional",
            "degraded_ok",
        }

    def _episode_map(episodes: list[dict]) -> dict[str, dict]:
        mapped: dict[str, dict] = {}
        for episode in episodes:
            episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
            if episode_id:
                mapped[episode_id] = episode
        return mapped

    def _required_failed_handoffs(handoffs: list[dict], episodes: list[dict]) -> list[dict]:
        by_id = _episode_map(episodes)
        required: list[dict] = []
        for handoff in _failed_handoffs(handoffs):
            episode_id = _string_value(handoff.get("producerEpisodeId"), handoff.get("episodeId"))
            episode = by_id.get(episode_id) if episode_id else None
            if episode and _is_optional_episode(episode):
                continue
            required.append(handoff)
        return required

    def _failed_episodes(episodes: list[dict]) -> list[dict]:
        return [
            episode
            for episode in episodes
            if str(episode.get("state") or "").strip().lower() in {"failed", "cancelled", "canceled"}
        ]

    def _required_failed_episodes(episodes: list[dict]) -> list[dict]:
        return [episode for episode in _failed_episodes(episodes) if not _is_optional_episode(episode)]

    async def runtime_episode_wait_node(state):
        session_id, run_id, workspace_path = _state_runtime_identity(state)
        route_context = dict((state or {}).get("current_route_context") or {})
        if session_id:
            route_context.setdefault("session_id", session_id)
            route_context.setdefault("sessionId", session_id)
        if run_id:
            route_context.setdefault("run_id", run_id)
            route_context.setdefault("runId", run_id)
        if workspace_path:
            route_context.setdefault("workspace_path", workspace_path)
            route_context.setdefault("workspacePath", workspace_path)
        identity_update = {
            **({"session_id": session_id, "sessionId": session_id} if session_id else {}),
            **({"run_id": run_id, "runId": run_id} if run_id else {}),
            **({"workspace_path": workspace_path, "workspacePath": workspace_path} if workspace_path else {}),
        }
        started_at = time.monotonic()
        wait_started_wall = time.time()
        queue_deadline = started_at + max(0.1, RUNTIME_EPISODE_QUEUE_GRACE_SECONDS)
        deadline = started_at + max(queue_deadline - started_at, RUNTIME_EPISODE_WAIT_SECONDS)
        last_episodes: list[dict] = []

        while True:
            episodes = _load_relevant_episodes(route_context=route_context, session_id=session_id, run_id=run_id)
            if episodes:
                last_episodes = episodes
            route_context, handoffs = _merge_handoffs(route_context, episodes)
            active = _active_episodes(episodes)
            terminal = _terminal_episodes(episodes)
            if episodes and not active:
                failed_handoffs = _failed_handoffs(handoffs)
                degraded_handoffs = _degraded_handoffs(handoffs)
                failed_episodes = _failed_episodes(terminal or episodes)
                required_failed_handoffs = _required_failed_handoffs(handoffs, terminal or episodes)
                required_failed_episodes = _required_failed_episodes(terminal or episodes)
                if required_failed_handoffs or required_failed_episodes:
                    failure_reason = _string_value(
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("errorCode"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("error_code"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("errorMessage"),
                        (required_failed_episodes[0] if required_failed_episodes else {}).get("error_message"),
                        (required_failed_handoffs[0] if required_failed_handoffs else {}).get("errorCode"),
                        (required_failed_handoffs[0] if required_failed_handoffs else {}).get("compactSummary"),
                        "runtime_episode_failed",
                    )
                    failure_key = _failure_summary_key(
                        episodes=required_failed_episodes or terminal or episodes,
                        handoffs=required_failed_handoffs,
                        reason=failure_reason,
                    )
                    notified_keys = {
                        str(item).strip()
                        for item in list(route_context.get("runtimeFailureSummaryKeys") or [])
                        if str(item).strip()
                    }
                    first_notification = failure_key not in notified_keys
                    route_context["runtimeFailureSummaryKeys"] = list(dict.fromkeys([*notified_keys, failure_key]))[-50:]
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_failed",
                                "episodeCount": len(episodes),
                                "handoffCount": len(handoffs),
                                "failedEpisodeCount": len(required_failed_episodes),
                                "failedHandoffCount": len(required_failed_handoffs),
                                "degradedEpisodeCount": len(failed_episodes) - len(required_failed_episodes),
                                "degradedHandoffCount": len(degraded_handoffs),
                                "reason": failure_reason,
                                "failureSummaryInjected": first_notification,
                            },
                            "messages": (
                                [
                                    _summary_message(
                                        episodes=required_failed_episodes or terminal or episodes,
                                        handoffs=required_failed_handoffs or handoffs,
                                        status="Recoverable Failure",
                                        reason=failure_reason,
                                    )
                                ]
                                if first_notification
                                else []
                            ),
                        },
                    )
                degraded_count = len(failed_episodes) + len(failed_handoffs) + len(degraded_handoffs)
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "runtime_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "resume_supervisor",
                            "state": "degraded_handoff_ready" if degraded_count else ("handoff_ready" if handoffs else "episode_terminal"),
                            "episodeCount": len(episodes),
                            "handoffCount": len(handoffs),
                            "degradedEpisodeCount": len(failed_episodes),
                            "degradedHandoffCount": len(failed_handoffs) + len(degraded_handoffs),
                        },
                        "messages": [
                            _summary_message(
                                episodes=terminal or episodes,
                                handoffs=handoffs,
                                status="Degraded Handoff Ready" if degraded_count else "Handoff Ready",
                                reason="optional_lane_degraded" if degraded_count else "",
                            )
                        ],
                    },
                )

            if active:
                active_states = {str(episode.get("state") or "") for episode in active}
                only_unclaimed_queue = active_states <= {"detected", "routed", "queued"}
                queue_grace_elapsed = all(
                    _episode_queue_age_seconds(episode, default_started_wall=wait_started_wall) >= RUNTIME_EPISODE_QUEUE_GRACE_SECONDS
                    for episode in active
                )
                if only_unclaimed_queue and queue_grace_elapsed:
                    if _has_live_bound_episode_lease() and time.monotonic() < deadline:
                        await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                        continue
                    failed_episodes: list[dict] = []
                    for episode in active:
                        episode_id = _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                        if not episode_id:
                            continue
                        try:
                            failed = db.complete_runtime_episode(
                                episode_id,
                                state="failed",
                                error_code="episode_runner_unavailable",
                                error_message="Runtime episode stayed queued and was not claimed by EpisodeRunner within the queue grace window.",
                                metadata={"recoverable": True, "source": "runtime_episode_wait"},
                            )
                            failed_episodes.append(dict(failed or episode))
                            emit_runtime_episode_event("runtime.episode.failed", {"episode": failed or episode})
                        except Exception:
                            failed_episodes.append(dict(episode))
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_runner_unavailable",
                                "episodeCount": len(failed_episodes),
                            },
                            "messages": [
                                _summary_message(
                                    episodes=failed_episodes,
                                    handoffs=[],
                                    status="Recoverable Failure",
                                    reason="episode_runner_unavailable",
                                )
                            ],
                        },
                    )
                if time.monotonic() >= deadline:
                    return Command(
                        goto="supervisor",
                        update={
                            "current_route_context": route_context,
                            **identity_update,
                            "runtime_dispatch_status": {
                                "mode": "runtime_episode",
                                "nextAction": "recoverable_failure",
                                "state": "episode_stalled",
                                "episodeCount": len(active),
                                "activeEpisodeIds": [
                                    _string_value(episode.get("episodeId"), episode.get("id"), episode.get("needId"))
                                    for episode in active
                                ],
                            },
                            "messages": [
                                _summary_message(
                                    episodes=active,
                                    handoffs=[],
                                    status="Recoverable Failure",
                                    reason="episode_stalled",
                                )
                            ],
                        },
                    )
                await asyncio.sleep(max(0.1, RUNTIME_EPISODE_POLL_SECONDS))
                continue

            return Command(
                goto="supervisor",
                update={
                    "current_route_context": route_context,
                    **identity_update,
                    "runtime_dispatch_status": {
                        "mode": "runtime_episode",
                        "nextAction": "resume_supervisor",
                        "state": "no_active_episode",
                        "episodeCount": len(last_episodes),
                    },
                },
            )

    return runtime_episode_wait_node


def compile_supervisor_workflow(
    *,
    agent_state_type,
    supervisor_node,
    supervisor_tools: list,
    agent_nodes_map: dict,
    create_routed_tool_node,
    checkpointer=None,
):
    workflow = StateGraph(agent_state_type)
    parallel_task_node = build_parallel_delegate_task_node(agent_nodes_map)
    parallel_join_node = build_parallel_delegate_join_node()

    workflow.add_node("workflow_entry", _workflow_entry_command)
    workflow.add_node("runtime_episode", build_runtime_episode_wait_node())
    workflow.add_node("supervisor", supervisor_node)

    async def supervisor_tools_node(state):
        visible_tools = filter_visible_tools_for_actor(
            supervisor_tools,
            actor="supervisor",
            route_context=dict((state or {}).get("current_route_context") or {}),
        )
        routed = create_routed_tool_node(visible_tools, name="supervisor_tools", fallback_goto="supervisor")
        command = await routed(state)
        return _route_runtime_tool_commands(command)

    workflow.add_node("supervisor_tools", supervisor_tools_node)
    workflow.add_node("parallel_delegate_task", parallel_task_node)
    workflow.add_node("parallel_delegate_join", parallel_join_node)
    workflow.set_entry_point("workflow_entry")

    for agent_id, agent_data in agent_nodes_map.items():
        workflow.add_node(agent_id, agent_data["node_func"])

        tool_node_name = f"{agent_id}_tools"
        if agent_data["tools"]:
            workflow.add_node(tool_node_name, agent_data.get("tool_node_func") or create_routed_tool_node(agent_data["tools"], name=tool_node_name, fallback_goto=agent_id))

        if agent_data.get("reflection_enabled") and agent_data.get("reviewer_func"):
            workflow.add_node(f"{agent_id}_reviewer", agent_data["reviewer_func"])

    if checkpointer is None:
        if runtime_stability_service.strict_supervisor_durability():
            raise RuntimeError("Supervisor workflow requires an explicit durable checkpointer; MemorySaver fallback is disabled.")
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()

    return workflow.compile(checkpointer=checkpointer)
