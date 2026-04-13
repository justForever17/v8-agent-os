from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, Send
from pydantic import BaseModel, Field

from core.context.delegation import build_delegation_context, latest_delegation_context
from .route_context import merge_route_context

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_state_update(state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    if not update:
        return state
    merged = dict(state)
    for key, value in update.items():
        if value is None:
            continue
        if key in {"messages", "todos", "delegation_contexts", "parallel_results", "parallel_invocations"}:
            merged[key] = list(merged.get(key) or []) + list(value or [])
        elif key == "current_route_context":
            merged[key] = merge_route_context(
                dict(merged.get("current_route_context") or {}),
                dict(value or {}),
            )
        else:
            merged[key] = value
    return merged


def build_delegate_parallel_tool(loaded_agents: list[dict[str, Any]]):
    agent_directory = {
        str(agent.get("id") or "").strip(): str(agent.get("name") or agent.get("id") or "").strip()
        for agent in list(loaded_agents or [])
        if str(agent.get("id") or "").strip() and str(agent.get("id") or "").strip() != "supervisor"
    }

    class ParallelDelegationItem(BaseModel):
        agent_id: str = Field(description="Registered target agent id.")
        reason: str = Field(description="Concrete delegated task for that agent.")

    class DelegateParallelInput(BaseModel):
        tasks: list[ParallelDelegationItem] = Field(
            description="Parallel delegation items. Maximum 2 subtasks per call.",
            min_length=1,
            max_length=2,
        )

    def delegate_parallel(
        tasks: list[dict[str, str]],
        tool_call_id: Annotated[str, InjectedToolCallId],
        state: Annotated[dict[str, Any], InjectedState],
    ) -> Command:
        if not tasks:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="Error: delegate_parallel requires at least one subtask.",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

        if len(tasks) > 2:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="Error: delegate_parallel supports at most 2 concurrent subtasks.",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

        normalized_tasks = [
            {
                "agent_id": str(item.get("agent_id") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
            }
            for item in list(tasks or [])
        ]
        invalid_agents = [item["agent_id"] for item in normalized_tasks if item["agent_id"] not in agent_directory]
        if invalid_agents:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Error: unknown agent ids: {', '.join(invalid_agents)}",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

        invocation_id = f"parallel_{uuid.uuid4().hex[:12]}"
        base_state = dict(state or {})
        base_messages = list(base_state.get("messages") or [])
        base_todos = list(base_state.get("todos") or [])
        base_contexts = list(base_state.get("delegation_contexts") or [])
        inherited_context = dict(base_state.get("current_route_context") or {})
        if not inherited_context:
            inherited_context = latest_delegation_context(base_contexts, agent_id=None)
        sends: list[Send] = []
        summary_lines: list[str] = []

        for index, spec in enumerate(normalized_tasks):
            agent_name = agent_directory[spec["agent_id"]]
            branch_context = build_delegation_context(
                agent_id=spec["agent_id"],
                agent_name=agent_name,
                query=spec["reason"],
                mode="parallel",
                source_runtime_kind=inherited_context.get("sourceRuntimeKind"),
                selected_skill_names=inherited_context.get("selectedSkillNames"),
                selected_skill_entries=inherited_context.get("selectedSkillEntries"),
                selected_mcp_tools=inherited_context.get("selectedMcpTools"),
                selected_plugin_host_tools=inherited_context.get("selectedPluginHostTools"),
                selected_baseline_tools=inherited_context.get("selectedBaselineTools"),
                prompt_addition=inherited_context.get("promptAddition"),
                invocation_id=invocation_id,
            )
            branch_state = dict(base_state)
            branch_state["messages"] = base_messages + [
                HumanMessage(content=f"[Supervisor Delegated Task to {agent_name}]:\n{spec['reason']}")
            ]
            branch_state["todos"] = list(base_todos)
            branch_state["delegation_contexts"] = base_contexts + [branch_context]
            branch_state["current_route_context"] = merge_route_context(inherited_context, branch_context)
            branch_state["parallel_branch"] = {
                "invocationId": invocation_id,
                "branchIndex": index,
                "agentId": spec["agent_id"],
                "agentName": agent_name,
                "reason": spec["reason"],
                "initialMessageCount": len(base_messages) + 1,
                "initialTodoCount": len(base_todos),
            }
            sends.append(Send("parallel_delegate_task", branch_state))
            summary_lines.append(f"- {agent_name}: {spec['reason']}")

        ack = ToolMessage(
            content=(
                f"Queued {len(sends)} concurrent delegations.\n"
                f"Invocation: {invocation_id}\n"
                + "\n".join(summary_lines)
            ),
            tool_call_id=tool_call_id,
        )
        return Command(
            goto=sends,
            update={
                "messages": [ack],
                "parallel_invocations": [
                    {
                        "invocationId": invocation_id,
                        "expected": len(sends),
                        "createdAt": _now_iso(),
                    }
                ],
            },
        )

    return StructuredTool.from_function(
        func=delegate_parallel,
        name="delegate_parallel",
        description="Delegate up to two registered sub-agents concurrently, then join their results back to the supervisor.",
        args_schema=DelegateParallelInput,
    )


async def _run_parallel_agent_branch(state: dict[str, Any], agent_data: dict[str, Any]) -> tuple[list[Any], list[Any], dict[str, Any]]:
    branch = dict(state.get("parallel_branch") or {})
    agent_id = str(branch.get("agentId") or "")
    current_node = agent_id
    local_state = dict(state)
    local_state["messages"] = list(state.get("messages") or [])
    local_state["todos"] = list(state.get("todos") or [])
    initial_message_count = int(branch.get("initialMessageCount") or len(local_state["messages"]))
    initial_todo_count = int(branch.get("initialTodoCount") or len(local_state["todos"]))

    max_steps = 24
    for _ in range(max_steps):
        if current_node == agent_id:
            result = agent_data["node_func"](local_state)
        elif current_node == f"{agent_id}_tools":
            tool_node = agent_data.get("tool_node_func")
            if tool_node is None:
                raise RuntimeError(f"{agent_id} 没有可用的工具节点。")
            result = await tool_node(local_state)
        elif current_node == f"{agent_id}_reviewer":
            reviewer = agent_data.get("reviewer_func")
            if reviewer is None:
                raise RuntimeError(f"{agent_id} 没有可用的 reviewer 节点。")
            result = reviewer(local_state)
        else:
            raise RuntimeError(f"{agent_id} 进入了未识别的并发分支节点：{current_node}")

        if not isinstance(result, Command):
            raise RuntimeError(f"{agent_id} 并发分支返回了非 Command 结果。")

        local_state = _merge_state_update(local_state, getattr(result, "update", None) or {})
        goto = getattr(result, "goto", None)
        if isinstance(goto, str):
            if goto == "supervisor":
                break
            current_node = goto
            continue
        raise RuntimeError(f"{agent_id} 并发分支返回了不支持的 goto 类型。")
    else:
        raise RuntimeError(f"{agent_id} 并发分支超过最大步数限制。")

    delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
    delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
    summary = {
        "invocationId": branch.get("invocationId"),
        "agentId": agent_id,
        "agentName": branch.get("agentName") or agent_id,
        "branchIndex": branch.get("branchIndex"),
        "status": "ok",
        "completedAt": _now_iso(),
        "messageCount": len(delta_messages),
        "todoDeltaCount": len(delta_todos),
        "toolMode": agent_data.get("tool_mode"),
    }
    return delta_messages, delta_todos, summary


def build_parallel_delegate_task_node(agent_nodes_map: dict[str, Any]):
    async def parallel_delegate_task(state: dict[str, Any]) -> Command:
        branch = dict(state.get("parallel_branch") or {})
        agent_id = str(branch.get("agentId") or "")
        agent_data = agent_nodes_map.get(agent_id)
        if not agent_data:
            return Command(
                goto="parallel_delegate_join",
                update={
                    "parallel_results": [
                        {
                            "invocationId": branch.get("invocationId"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "branchIndex": branch.get("branchIndex"),
                            "status": "error",
                            "error": f"未找到子 Agent '{agent_id}'。",
                            "completedAt": _now_iso(),
                        }
                    ]
                },
            )

        try:
            delta_messages, delta_todos, summary = await _run_parallel_agent_branch(state, agent_data)
            return Command(
                goto="parallel_delegate_join",
                update={
                    "messages": delta_messages,
                    "todos": delta_todos,
                    "parallel_results": [summary],
                },
            )
        except Exception as exc:
            failure_message = HumanMessage(
                content=(
                    f"[{branch.get('agentName') or agent_id} 并发执行异常]\n"
                    f"错误: {type(exc).__name__}: {str(exc).strip() or 'Unknown error'}"
                ),
                id=str(uuid.uuid4()),
            )
            return Command(
                goto="parallel_delegate_join",
                update={
                    "messages": [failure_message],
                    "parallel_results": [
                        {
                            "invocationId": branch.get("invocationId"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "branchIndex": branch.get("branchIndex"),
                            "status": "error",
                            "error": str(exc).strip() or exc.__class__.__name__,
                            "completedAt": _now_iso(),
                        }
                    ],
                },
            )

    return parallel_delegate_task


def build_parallel_delegate_join_node():
    def parallel_delegate_join(state: dict[str, Any]) -> Command:
        invocations = list(state.get("parallel_invocations") or [])
        latest = invocations[-1] if invocations else {}
        invocation_id = str(latest.get("invocationId") or "").strip()
        expected = int(latest.get("expected") or 0)
        results = [
            dict(item)
            for item in list(state.get("parallel_results") or [])
            if str(item.get("invocationId") or "").strip() == invocation_id
        ]
        if not results:
            return Command(goto="supervisor", update={})

        failures = [item for item in results if item.get("status") != "ok"]
        summary = HumanMessage(
            content=(
                f"[并发委派完成]\n"
                f"Invocation: {invocation_id or 'n/a'}\n"
                f"已回收 {len(results)}/{expected or len(results)} 个并发子任务结果。\n"
                f"失败: {len(failures)} 个。\n"
                "请结合上方各子 Agent 回传结果继续决策。"
            ),
            id=str(uuid.uuid4()),
        )
        return Command(goto="supervisor", update={"messages": [summary]})

    return parallel_delegate_join
