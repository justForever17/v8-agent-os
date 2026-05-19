from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from core.runtime_tool_access import filter_visible_tools_for_actor
from core.delegation_broker import expand_delegation_task_briefs
from core.runtime_episodes import (
    build_runtime_episode,
    emit_runtime_episode_event,
    enqueue_runtime_episode,
    transition_runtime_episode,
    upsert_runtime_episode,
)
from erc.runtime_stability import runtime_stability_service
from .parallel_support import build_parallel_delegate_join_node, build_parallel_delegate_task_node


def build_planner_auto_dispatch_node():
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

    def _with_capability_episodes(route_context: dict, plan: dict, *, enqueue: bool = False) -> dict:
        updated = dict(route_context or {})
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
                persisted = enqueue_runtime_episode(episode, priority=int(item.get("priority") or 0))
                updated = upsert_runtime_episode(updated, {**episode, "state": persisted.get("state") or "queued"})
                emit_runtime_episode_event("runtime.episode.queued", {"episode": {**episode, "state": "queued"}})
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
        route_context = _with_capability_episodes(dict((state or {}).get("current_route_context") or {}), plan)
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
        route_context = _with_capability_episodes(route_context, plan, enqueue=True)
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
        return Command(goto="supervisor", update=update)

    return planner_auto_dispatch_node


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
    workflow.add_node("supervisor", supervisor_node)
    async def supervisor_tools_node(state):
        visible_tools = filter_visible_tools_for_actor(
            supervisor_tools,
            actor="supervisor",
            route_context=dict((state or {}).get("current_route_context") or {}),
        )
        routed = create_routed_tool_node(visible_tools, name="supervisor_tools", fallback_goto="supervisor")
        return await routed(state)

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
