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
from core.delegation_broker import build_minimal_task_brief
from core.native_tools import delegation_broker
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


def _compact_message_text(message: Any, *, limit: int = 900) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        text = "\n".join(part.strip() for part in parts if part.strip())
    else:
        text = str(content or "")
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())
    if len(normalized) > limit:
        return normalized[: limit - 3].rstrip() + "..."
    return normalized


def _compact_transcript(messages: list[Any], *, limit: int = 1800) -> str:
    chunks: list[str] = []
    for message in messages:
        text = _compact_message_text(message, limit=700)
        if not text:
            continue
        role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
        chunks.append(f"{role}: {text}")
    compact = "\n\n".join(chunks)
    if len(compact) > limit:
        return compact[: limit - 3].rstrip() + "..."
    return compact


def build_delegate_parallel_tool(loaded_agents: list[dict[str, Any]]):
    agent_directory = {
        str(agent.get("id") or "").strip(): str(agent.get("name") or agent.get("id") or "").strip()
        for agent in list(loaded_agents or [])
        if str(agent.get("id") or "").strip() and str(agent.get("id") or "").strip() != "supervisor"
    }

    class ParallelDelegationItem(BaseModel):
        agent_id: str = Field(description="Registered target agent id.")
        reason: str = Field(description="Concrete delegated task for that agent.")
        taskBriefId: str | None = Field(default=None, description="Optional planner task brief id for swarm projection.")

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
                "taskBriefId": str(item.get("taskBriefId") or item.get("task_brief_id") or "").strip(),
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

        broker_tasks = [
            build_minimal_task_brief(
                goal=spec["reason"],
                task_brief_id=spec.get("taskBriefId") or "",
                preferred_agent_id=spec["agent_id"],
                execution_lane_hint="subagent",
            )
            for spec in normalized_tasks
        ]
        return delegation_broker.func(
            mode="dispatch",
            tasks=broker_tasks,
            tool_call_id=tool_call_id,
            state={**dict(state or {}), "delegationCompatSource": "delegate_parallel"},
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
        "taskBriefId": branch.get("taskBriefId"),
        "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
        "taskGoal": branch.get("reason"),
        "agentId": agent_id,
        "agentName": branch.get("agentName") or agent_id,
        "delegationId": branch.get("delegationId"),
        "lane": branch.get("lane") or "subagent",
        "targetId": agent_id,
        "targetLabel": branch.get("agentName") or agent_id,
        "branchIndex": branch.get("branchIndex"),
        "status": "ok",
        "completedAt": _now_iso(),
        "messageCount": len(delta_messages),
        "todoDeltaCount": len(delta_todos),
        "toolMode": agent_data.get("tool_mode"),
        "compactTranscript": _compact_transcript(delta_messages),
        "localSelfCheck": "Subagent branch completed; supervisor must still accept, retry, or ignore the result.",
        "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
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
                            "taskBriefId": branch.get("taskBriefId"),
                            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                            "taskGoal": branch.get("reason"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "delegationId": branch.get("delegationId"),
                            "lane": branch.get("lane") or "subagent",
                            "targetId": agent_id,
                            "targetLabel": branch.get("agentName") or agent_id,
                            "branchIndex": branch.get("branchIndex"),
                            "status": "error",
                            "error": f"未找到子 Agent '{agent_id}'。",
                            "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
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
                    "todos": delta_todos,
                    "parallel_results": [summary],
                },
            )
        except Exception as exc:
            return Command(
                goto="parallel_delegate_join",
                update={
                    "parallel_results": [
                        {
                            "invocationId": branch.get("invocationId"),
                            "taskBriefId": branch.get("taskBriefId"),
                            "taskBrief": branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else None,
                            "taskGoal": branch.get("reason"),
                            "agentId": agent_id,
                            "agentName": branch.get("agentName") or agent_id,
                            "delegationId": branch.get("delegationId"),
                            "lane": branch.get("lane") or "subagent",
                            "targetId": agent_id,
                            "targetLabel": branch.get("agentName") or agent_id,
                            "branchIndex": branch.get("branchIndex"),
                            "status": "error",
                            "error": str(exc).strip() or exc.__class__.__name__,
                            "localSelfCheck": "Subagent branch failed before supervisor acceptance.",
                            "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
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
                "详细 compact transcript、产物引用与局部自检已投影到 subagent_swarm runtime card；最终采纳、重试或忽略仍由 supervisor 决定。"
            ),
            id=str(uuid.uuid4()),
        )
        return Command(goto="supervisor", update={"messages": [summary]})

    return parallel_delegate_join
