from langgraph.graph import StateGraph
from langgraph.types import Command

from erc.runtime_stability import runtime_stability_service
from .parallel_support import build_parallel_delegate_join_node, build_parallel_delegate_task_node


def build_planner_auto_dispatch_node():
    def planner_auto_dispatch_node(state):
        plan = dict((state or {}).get("planner_plan") or {})
        decision = dict(plan.get("autoDispatchDecision") or {})
        if not bool(decision.get("willDispatch")):
            return Command(
                goto="supervisor",
                update={
                    "planner_dispatch_status": {
                        "mode": str(decision.get("mode") or "suggest"),
                        "willDispatch": False,
                        "reason": str(decision.get("reason") or "not_eligible"),
                    }
                },
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
        update["planner_dispatch_status"] = {
            "mode": str(decision.get("mode") or "auto"),
            "dispatched": True,
            "reason": str(decision.get("reason") or "eligible"),
            "planId": plan.get("planId"),
            "taskCount": len(list(plan.get("taskBriefs") or [])),
        }
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
    workflow.add_node(
        "supervisor_tools",
        create_routed_tool_node(supervisor_tools, name="supervisor_tools", fallback_goto="supervisor"),
    )
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
