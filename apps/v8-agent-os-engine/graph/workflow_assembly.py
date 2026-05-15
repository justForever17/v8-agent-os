from langgraph.graph import StateGraph
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from core.runtime_tool_access import filter_visible_tools_for_actor
from erc.runtime_stability import runtime_stability_service
from .parallel_support import build_parallel_delegate_join_node, build_parallel_delegate_task_node


def build_planner_auto_dispatch_node():
    def planner_auto_dispatch_node(state):
        plan = dict((state or {}).get("planner_plan") or {})
        route_context = dict((state or {}).get("current_route_context") or {})
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
                    "messages": [
                        HumanMessage(
                            content=(
                                "[Planner Auto Dispatch Blocked]\n"
                                "用户显式要求 Engineering Runtime，但 Engineering Runtime 当前被禁用。"
                                "Supervisor 不应继续写文件、安装依赖或运行构建命令；请让用户启用 Engineering Runtime，"
                                "或由用户明确批准 direct exception。"
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
            if blocked:
                update["messages"] = [
                    HumanMessage(
                        content=(
                            "[Planner Auto Dispatch Blocked]\n"
                            f"自动派发被阻断：{reason}。Supervisor 不应继续批量写文件、安装依赖或运行构建命令；"
                            "请配置工程子代理/worker、修复任务 writeSet，或请求用户批准 direct exception。"
                        )
                    )
                ]
            return Command(
                goto="supervisor",
                update=update,
            )
        if dict((state or {}).get("planner_dispatch_status") or {}).get("dispatched"):
            return Command(goto="supervisor", update={})
        from core.native_tools import delegation_broker

        dispatch_state = {
            **dict(state or {}),
            "delegationDispatchSource": "planner_auto_dispatch",
        }
        command = delegation_broker.func(
            mode="dispatch",
            tasks=list(plan.get("taskBriefs") or []),
            state=dispatch_state,
            tool_call_id=f"planner_auto:{str(plan.get('planId') or 'plan')}",
        )
        update = dict(getattr(command, "update", None) or {})
        # Auto-dispatch is an internal orchestration step. Keep process/swarm
        # projection inputs, but do not inject a synthetic broker ToolMessage
        # into the supervisor narrative chain.
        update.pop("messages", None)
        parallel_results = [item for item in list(update.get("parallel_results") or []) if isinstance(item, dict)]
        failed_results = [
            item
            for item in parallel_results
            if str(item.get("status") or "").strip().lower() in {"error", "blocked", "failed"}
        ]
        no_matching_target = any(str(item.get("error") or "").strip() == "no_matching_target" for item in failed_results)
        workset_blocked = any(str(item.get("error") or "").strip() == "workset_dispatch_blocked" for item in failed_results)
        dispatch_blocked = bool(parallel_results) and len(failed_results) == len(parallel_results) and (no_matching_target or workset_blocked)
        update["planner_dispatch_status"] = {
            "mode": str(decision.get("mode") or "auto"),
            "dispatched": True,
            "blocked": dispatch_blocked,
            "reason": str(decision.get("reason") or "eligible"),
            **({"blockedReason": "no_matching_target" if no_matching_target else "workset_dispatch_blocked"} if dispatch_blocked else {}),
            "planId": plan.get("planId"),
            "taskCount": len(list(plan.get("taskBriefs") or [])),
        }
        if dispatch_blocked:
            update.setdefault("messages", []).append(
                HumanMessage(
                    content=(
                        "[Planner Auto Dispatch Blocked]\n"
                        "自动派发没有找到可用的工程 subagent / external worker，或写集治理阻断了派发。"
                        "Supervisor 不应继续批量写文件、安装依赖或运行构建命令；请配置工程子代理/worker，"
                        "或请求用户批准 direct exception。"
                    )
                )
            )
        return Command(goto=getattr(command, "goto", None) or "supervisor", update=update)

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
