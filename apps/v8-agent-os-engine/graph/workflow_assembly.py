import hashlib

import asyncio
import os
import time

from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from core.database import db
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.delegation_broker import expand_delegation_task_briefs
from core.runtime_episodes import (
    ACTIVE_EPISODE_STATES,
    TERMINAL_EPISODE_STATES,
    append_handoff_ref,
    build_runtime_episode,
    emit_runtime_episode_event,
    enqueue_runtime_episode,
    transition_runtime_episode,
    upsert_runtime_episode,
)
from core.time_truth import utc_now_iso
from erc.runtime_context import get_runtime_context
from erc.runtime_stability import runtime_stability_service
from .parallel_support import build_parallel_delegate_join_node, build_parallel_delegate_task_node


RUNTIME_EPISODE_WAIT_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_WAIT_SECONDS", "600"))
RUNTIME_EPISODE_QUEUE_GRACE_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_QUEUE_GRACE_SECONDS", "60"))
RUNTIME_EPISODE_POLL_SECONDS = float(os.getenv("V8_RUNTIME_EPISODE_POLL_SECONDS", "0.8"))


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


def build_planner_auto_dispatch_node():
    def _episode_identity(*, session_id: str | None, run_id: str | None, plan: dict, item: dict, kind: str) -> tuple[str, str]:
        seed = "|".join(
            [
                str(session_id or ""),
                str(run_id or ""),
                str(plan.get("planId") or ""),
                str(item.get("taskBriefId") or item.get("id") or item.get("title") or ""),
                str(kind or ""),
                str(item.get("reason") or item.get("capability") or ""),
            ]
        )
        digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]
        episode_id = str(item.get("episodeId") or item.get("needId") or f"episode_{digest}")
        idempotency_key = str(item.get("idempotencyKey") or f"planner:{run_id or session_id or 'no_run'}:{digest}")
        return episode_id, idempotency_key

    def _matching_task_briefs(plan: dict, item: dict) -> list[dict]:
        briefs = [dict(brief) for brief in list(plan.get("taskBriefs") or []) if isinstance(brief, dict)]
        task_brief_id = str(item.get("taskBriefId") or "").strip()
        if task_brief_id:
            matched = [
                brief
                for brief in briefs
                if str(brief.get("id") or brief.get("taskBriefId") or brief.get("title") or "").strip() == task_brief_id
            ]
            if matched:
                return matched
        kind = str(item.get("kind") or "").strip()
        if kind == "delegation":
            return briefs
        return briefs[:1] if briefs else []

    def _with_capability_episodes(
        route_context: dict,
        plan: dict,
        *,
        enqueue: bool = False,
        session_id: str | None = None,
        run_id: str | None = None,
        workspace_path: str | None = None,
    ) -> dict:
        updated = dict(route_context or {})
        if session_id:
            updated.setdefault("sessionId", session_id)
            updated.setdefault("session_id", session_id)
        if run_id:
            updated.setdefault("runId", run_id)
            updated.setdefault("run_id", run_id)
        if workspace_path:
            updated.setdefault("workspacePath", workspace_path)
            updated.setdefault("workspace_path", workspace_path)
        for item in list(plan.get("capabilityPlan") or []):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            if not kind:
                continue
            state = str(item.get("state") or ("queued" if enqueue else "detected"))
            matching_briefs = _matching_task_briefs(plan, item)
            need_payload = {**item, "source": item.get("source") or "planner"}
            inputs = dict(need_payload.get("inputs") or {})
            if matching_briefs:
                inputs.setdefault("taskBriefs", matching_briefs)
                if kind == "delegation":
                    inputs.setdefault("workerBriefs", matching_briefs)
                    inputs.setdefault("targetCount", len(matching_briefs))
            if workspace_path:
                inputs.setdefault("workspacePath", workspace_path)
            episode_id, idempotency_key = _episode_identity(
                session_id=session_id,
                run_id=run_id,
                plan=plan,
                item=item,
                kind=kind,
            )
            need_payload.setdefault("episodeId", episode_id)
            need_payload.setdefault("needId", episode_id)
            need_payload.setdefault("idempotencyKey", idempotency_key)
            if session_id:
                need_payload.setdefault("sessionId", session_id)
                need_payload.setdefault("session_id", session_id)
            if run_id:
                need_payload.setdefault("runId", run_id)
                need_payload.setdefault("run_id", run_id)
            if inputs:
                need_payload["inputs"] = inputs
            episode = build_runtime_episode(
                need=need_payload,
                kind=kind,
                state=state,
                required_runtime_access=list(item.get("requiredRuntimeAccess") or []),
                continuation_target=str(item.get("continuationTarget") or "planner_auto_dispatch"),
                extra={"taskBriefId": str(item.get("taskBriefId") or "")},
            )
            if session_id:
                episode["sessionId"] = session_id
                episode["session_id"] = session_id
            if run_id:
                episode["runId"] = run_id
                episode["run_id"] = run_id
            item["episodeId"] = episode.get("episodeId")
            item["needId"] = episode.get("needId")
            before_ids = {
                str(existing_item.get("episodeId") or existing_item.get("needId") or "")
                for existing_item in list(updated.get("capabilityEpisodes") or [])
                if isinstance(existing_item, dict)
            }
            updated = upsert_runtime_episode(updated, episode)
            if str(episode.get("episodeId") or "") not in before_ids:
                emit_runtime_episode_event("capability.need.detected", {"episode": episode})
            if enqueue:
                persisted = enqueue_runtime_episode(
                    episode,
                    session_id=session_id,
                    run_id=run_id,
                    priority=int(item.get("priority") or 0),
                )
                merged_episode = {
                    **episode,
                    **{
                        k: v
                        for k, v in dict(persisted or {}).items()
                        if k
                        in {
                            "session_id",
                            "sessionId",
                            "run_id",
                            "runId",
                            "state",
                            "lastHeartbeatAt",
                            "leaseGeneration",
                        }
                    },
                    "state": str((persisted or {}).get("state") or "queued"),
                }
                updated = upsert_runtime_episode(updated, merged_episode)
                emit_runtime_episode_event("runtime.episode.queued", {"episode": merged_episode})
        return updated

    def _mark_plan_episodes(route_context: dict, plan: dict, *, state: str, reason: str | None = None) -> dict:
        updated = dict(route_context or {})
        for item in list(plan.get("capabilityPlan") or []):
            if not isinstance(item, dict):
                continue
            episode_id = str(item.get("episodeId") or item.get("needId") or "").strip()
            if not episode_id:
                continue
            updated, episode = transition_runtime_episode(
                updated,
                episode_id,
                state=state,
                **({"statusReason": reason} if reason else {}),
            )
            if episode:
                topic = {
                    "active": "runtime.episode.started",
                    "waiting": "runtime.episode.waiting",
                    "failed": "runtime.episode.failed",
                    "completed": "runtime.episode.completed",
                }.get(state, "runtime.episode.progress")
                emit_runtime_episode_event(topic, {"episode": episode})
        return updated

    def planner_auto_dispatch_node(state):
        plan = dict((state or {}).get("planner_plan") or {})
        session_id, run_id, workspace_path = _state_runtime_identity(state)
        route_context = _with_capability_episodes(
            dict((state or {}).get("current_route_context") or {}),
            plan,
            session_id=session_id,
            run_id=run_id,
            workspace_path=workspace_path,
        )
        engineering_trigger = dict(route_context.get("engineeringTriggerDecision") or {})
        if route_context.get("explicitEngineeringRequested") and engineering_trigger.get("reason") == "engineering_lane_disabled":
            return Command(
                goto="supervisor",
                update={
                    "planner_dispatch_status": {
                        "mode": "blocked",
                        "willDispatch": False,
                        "blocked": True,
                        "reason": "engineering_runtime_disabled",
                        "blockedReason": "engineering_runtime_disabled",
                    },
                    "current_route_context": route_context,
                    "messages": [
                        HumanMessage(
                            content=(
                                "[Planner Auto Dispatch Blocked]\n"
                                "用户显式要求 Engineering Runtime，但 Engineering Runtime 当前被禁用。"
                                "Supervisor 不应继续写文件、安装依赖或运行构建命令；请让用户启用 Engineering Runtime，"
                                "复杂工程任务不能用 direct exception 绕过 Engineering 主链。"
                            )
                        )
                    ],
                },
            )
        decision = dict(plan.get("autoDispatchDecision") or {})
        if not bool(decision.get("willDispatch")):
            reason = str(decision.get("reason") or "not_eligible")
            blocked = reason in {"no_matching_target", "write_set_conflict", "planner_quality_flags_block_dispatch"}
            update = {
                "planner_dispatch_status": {
                    "mode": str(decision.get("mode") or "suggest"),
                    "willDispatch": False,
                    "blocked": blocked,
                    "reason": reason,
                    **({"blockedReason": reason} if blocked else {}),
                }
            }
            update["current_route_context"] = route_context
            if blocked:
                update["current_route_context"] = _mark_plan_episodes(
                    route_context,
                    plan,
                    state="failed",
                    reason=reason,
                )
                update["messages"] = [
                    HumanMessage(
                        content=(
                            "[Planner Auto Dispatch Blocked]\n"
                            f"自动派发被阻断：{reason}。Supervisor 不应继续批量写文件、安装依赖或运行构建命令；"
                            "请配置工程子代理/worker、修复任务 writeSet，或改走 Engineering/delegation。"
                        )
                    )
                ]
            return Command(
                goto="supervisor",
                update=update,
            )
        if dict((state or {}).get("planner_dispatch_status") or {}).get("dispatched"):
            return Command(goto="supervisor", update={"current_route_context": route_context})
        route_context = _with_capability_episodes(
            route_context,
            plan,
            enqueue=True,
            session_id=session_id,
            run_id=run_id,
            workspace_path=workspace_path,
        )
        update = {"current_route_context": route_context}
        queued_episodes = [
            item
            for item in list(route_context.get("capabilityEpisodes") or [])
            if isinstance(item, dict) and str(item.get("state") or "") == "queued"
        ]
        dispatch_blocked = not bool(queued_episodes)
        update["planner_dispatch_status"] = {
            "mode": str(decision.get("mode") or "auto"),
            "dispatched": True,
            "blocked": dispatch_blocked,
            "reason": str(decision.get("reason") or "eligible"),
            **({"blockedReason": "no_runtime_episode_queued"} if dispatch_blocked else {}),
            "planId": plan.get("planId"),
            "macroTaskCount": len(list(plan.get("taskBriefs") or [])),
            "taskCount": len(expand_delegation_task_briefs(plan.get("taskBriefs") or [])),
            "episodeCount": len(queued_episodes),
        }
        update["current_route_context"] = _mark_plan_episodes(
            dict(update.get("current_route_context") or route_context),
            plan,
            state="queued" if not dispatch_blocked else "failed",
            reason="runtime_episode_queued" if not dispatch_blocked else "dispatch_blocked",
        )
        if dispatch_blocked:
            update.setdefault("messages", []).append(
                HumanMessage(
                    content=(
                        "[Planner Auto Dispatch Blocked]\n"
                        "自动派发没有找到可用的工程 subagent / external worker，或写集治理阻断了派发。"
                        "Supervisor 不应继续批量写文件、安装依赖或运行构建命令；请配置工程子代理/worker，"
                        "或改走 Engineering/delegation。"
                    )
                )
            )
        return Command(goto="runtime_episode", update=update)

    return planner_auto_dispatch_node


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
                by_id[str(episode.get("episodeId") or episode.get("id") or episode_id)] = dict(episode)
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
                by_id[episode_id] = dict(episode)
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

    def _summary_message(*, episodes: list[dict], handoffs: list[dict], status: str, reason: str = "") -> HumanMessage:
        lines = [f"[Runtime Episode {status}]"]
        if reason:
            lines.append(f"Reason: {reason}")
        if handoffs:
            lines.append("Typed handoffs:")
            for handoff in handoffs[:8]:
                kind = _string_value(handoff.get("kind"), "runtime_handoff")
                summary = _string_value(handoff.get("compactSummary"), handoff.get("summary"))[:800]
                lines.append(f"- {kind}: {summary}")
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
        return HumanMessage(content="\n".join(lines))

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
                return Command(
                    goto="supervisor",
                    update={
                        "current_route_context": route_context,
                        **identity_update,
                        "planner_dispatch_status": {
                            "mode": "runtime_episode",
                            "nextAction": "resume_supervisor",
                            "state": "handoff_ready" if handoffs else "episode_terminal",
                            "episodeCount": len(episodes),
                            "handoffCount": len(handoffs),
                        },
                        "messages": [_summary_message(episodes=terminal or episodes, handoffs=handoffs, status="Handoff Ready")],
                    },
                )

            if active:
                active_states = {str(episode.get("state") or "") for episode in active}
                only_unclaimed_queue = active_states <= {"detected", "routed", "queued"}
                if only_unclaimed_queue and time.monotonic() >= queue_deadline:
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
                            "planner_dispatch_status": {
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
                            "planner_dispatch_status": {
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
                    "planner_dispatch_status": {
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

    workflow.add_node("planner_auto_dispatch", build_planner_auto_dispatch_node())
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
        update = dict(getattr(command, "update", None) or {})
        planner_status = dict(update.get("planner_dispatch_status") or {})
        messages = list(update.get("messages") or [])
        should_wait = str(planner_status.get("nextAction") or "").strip() == "wait_episode"
        for message in messages:
            additional = dict(getattr(message, "additional_kwargs", None) or {})
            if str(additional.get("recommendedNextAction") or "").strip() == "wait_episode":
                should_wait = True
                break
        if should_wait:
            return Command(goto="runtime_episode", update=update)
        return command

    workflow.add_node("supervisor_tools", supervisor_tools_node)
    workflow.add_node("parallel_delegate_task", parallel_task_node)
    workflow.add_node("parallel_delegate_join", parallel_join_node)
    workflow.set_entry_point("planner_auto_dispatch")

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
