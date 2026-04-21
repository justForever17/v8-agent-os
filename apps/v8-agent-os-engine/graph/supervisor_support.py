from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage
from langgraph.types import Command

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

__all__ = [
    "build_agent_runtime_failure_command",
    "extract_task_context",
    "resolve_todos",
]
