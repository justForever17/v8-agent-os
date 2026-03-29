from __future__ import annotations

import re
import uuid
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.types import Command

from core.storage import storage
from .task_context import extract_task_context, resolve_todos


def build_agent_runtime_failure_command(*, agent_name: str, exc: Exception, goto: str = "supervisor") -> Command:
    error_type = type(exc).__name__
    error_text = str(exc).strip() or "Unknown runtime error"
    normalized = f"{error_type}: {error_text}"
    if len(normalized) > 1200:
        normalized = normalized[:1200] + "..."

    feedback_msg = HumanMessage(
        content=(
            f"[{agent_name} 执行异常]\n"
            f"错误: {normalized}\n"
            "[System Instruction]: A specialized agent/reviewer path failed unexpectedly. "
            "Do NOT crash the overall run. Decide whether to retry with a narrower task, "
            "select another tool/agent, or ask the user for clarification."
        ),
        id=str(uuid.uuid4()),
    )
    return Command(goto=goto, update={"messages": [feedback_msg]})

@tool
def create_agent(
    name: str,
    description: str,
    system_prompt: str,
    tools: Optional[list[str]] = None,
    model: Optional[str] = None,
) -> str:
    """
    创建一个新的专业子 Agent，并持久化到本地配置中，供后续对话轮次或编排流程继续复用。
    """
    explicit_tools = list(tools or [])
    tool_mode = "explicit" if explicit_tools else "contextual_auto"
    if tools is None:
        tools = []
    if not model:
        model = storage.get_default_agent_model_id() or ""

    safe_name = re.sub(r"[^\w\-]", "-", name.replace(" ", "-")).strip("-")
    safe_name = re.sub(r"-+", "-", safe_name)
    agent_id = f"{safe_name}-{uuid.uuid4().hex[:4]}" if safe_name else f"agent-{uuid.uuid4().hex[:8]}"

    config_dict = {
        "id": agent_id,
        "name": name,
        "description": description,
        "model": model,
        "tools": tools,
        "tool_mode": tool_mode,
        "system_prompt": system_prompt,
        "createdBy": "supervisor",
    }

    try:
        storage.save_agent(config_dict)
        return (
            f"Successfully created agent '{name}' with ID '{agent_id}' (tool_mode={tool_mode}). "
            "It will be available for you to delegate tasks to on the NEXT conversation turn."
        )
    except Exception as exc:
        return f"Failed to create agent: {str(exc)}"


__all__ = [
    "build_agent_runtime_failure_command",
    "create_agent",
    "extract_task_context",
    "resolve_todos",
]
