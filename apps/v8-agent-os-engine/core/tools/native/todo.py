from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import Command

from erc.runtime_context import get_runtime_context


@tool
def write_todos(task_name: str, plan_markdown: str, todos: list[str], tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Create a compact Supervisor orchestration checklist.

    Todos track this turn's cross-runtime milestones only: clarify, route, wait for handoff,
    merge, verify, and deliver. Do not put Spec documents, runtime-internal plans, file edits,
    research source steps, media render steps, desktop/RPA trace steps, or private subagent
    checklists here. In Spec Mode, use todos only for stage progress such as requirements
    alignment, approval waits, design research, dispatch, and final acceptance.

    Arguments:
        task_name: Short english dash-separated task name.
        plan_markdown: Concise orchestration summary and acceptance notes.
        todos: High-level orchestration milestone descriptions.
    """
    runtime_context = get_runtime_context()
    now_iso = datetime.now(timezone.utc).isoformat()
    task_id = f"task_{uuid.uuid4().hex[:12]}"

    normalized_todos: list[str] = []
    seen_texts: set[str] = set()
    for raw in list(todos or []):
        text = str(raw or "").strip()
        if not text:
            continue
        if len(text) > 240:
            text = text[:237].rstrip() + "..."
        lowered = text.lower()
        if lowered in seen_texts:
            continue
        seen_texts.add(lowered)
        normalized_todos.append(text)

    if not normalized_todos:
        normalized_todos = ["Clarify and continue the task plan."]

    init_marker = {
        "_task_init": True,
        "taskId": task_id,
        "name": task_name,
        "plan": plan_markdown,
        "runId": runtime_context.get("run_id"),
        "sessionId": runtime_context.get("session_id"),
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }
    todo_items = [
        {
            "id": f"{task_id}-item-{idx}",
            "text": text,
            "status": "pending",
            "order": idx,
            "createdAt": now_iso,
            "updatedAt": now_iso,
        }
        for idx, text in enumerate(normalized_todos)
    ]

    payload = [init_marker] + todo_items
    checklist = "\n".join([f"  [ ] {t}" for t in normalized_todos])

    return Command(
        update={
            "todos": payload,
            "messages": [
                ToolMessage(
                    content=f"✓ Persistent Task plan '{task_name}' created with {len(normalized_todos)} items:\n{checklist}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def update_todo(index: int, status: str, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
    """Mark a Supervisor orchestration todo as done, in progress, or skipped.

    Runtime-internal progress belongs to the relevant runtime card, episode, ledger, proof,
    job, or artifact rather than this checklist.
    """
    if status not in ("done", "in_progress", "skipped"):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"Error: Invalid status '{status}'. Must be 'done', 'in_progress', or 'skipped'.",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    icon = {"done": "✓", "in_progress": "→", "skipped": "⊘"}.get(status, "?")

    return Command(
        update={
            "todos": [{"_update": True, "index": index, "status": status, "updatedAt": datetime.now(timezone.utc).isoformat()}],
            "messages": [
                ToolMessage(
                    content=f"{icon} Todo #{index} marked as '{status}'.",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


__all__ = ["update_todo", "write_todos"]
