from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command, Send

from core.context.delegation import build_delegation_context, latest_delegation_context
from core.runtime_episodes import (
    append_handoff_ref,
    build_handoff_ref,
    build_runtime_episode,
    emit_runtime_episode_event,
    enqueue_runtime_episode,
    transition_runtime_episode,
    upsert_runtime_episode,
)
from erc.runtime_context import bind_runtime_context
from .route_context import merge_route_context

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_context_from_parallel_state(state: dict[str, Any], *, branch: dict[str, Any] | None = None) -> dict[str, Any]:
    state = dict(state or {})
    route_context = dict(state.get("current_route_context") or {})
    branch = dict(branch or state.get("parallel_branch") or {})
    context = {
        "runtime_kind": "subagent",
        "trigger_source": "delegation_broker",
        "session_id": state.get("session_id") or state.get("sessionId") or route_context.get("session_id") or route_context.get("sessionId"),
        "run_id": state.get("run_id") or state.get("runId") or route_context.get("run_id") or route_context.get("runId"),
        "workspace_path": state.get("workspace_path") or state.get("workspacePath") or route_context.get("workspace_path") or route_context.get("workspacePath"),
        "goal": branch.get("reason") or branch.get("taskGoal") or branch.get("taskBrief"),
        "delegation_id": branch.get("delegationId"),
        "subagent_id": branch.get("agentId"),
    }
    return {key: value for key, value in context.items() if value is not None and str(value).strip()}


def _merge_state_update(state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    if not update:
        return state
    merged = dict(state)
    for key, value in update.items():
        if value is None:
            continue
        if key in {"messages", "todos", "delegation_contexts", "parallel_results", "parallel_invocations", "pending_child_delegations"}:
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
        tool_names = _extract_tool_names_from_message(message)
        if not text and not tool_names:
            continue
        role = getattr(message, "type", None) or getattr(message, "role", None) or message.__class__.__name__
        if tool_names:
            tool_line = "使用工具: " + ", ".join(tool_names)
            text = f"{tool_line}\n{text}" if text else tool_line
        chunks.append(f"{role}: {text}")
    compact = "\n\n".join(chunks)
    if len(compact) > limit:
        return compact[: limit - 3].rstrip() + "..."
    return compact


def _extract_tool_names_from_message(message: Any) -> list[str]:
    names: list[str] = []

    def _add(value: Any) -> None:
        name = str(value or "").strip()
        if not name:
            return
        if name not in names:
            names.append(name)

    for call in list(getattr(message, "tool_calls", None) or []):
        if isinstance(call, dict):
            _add(call.get("name"))
        else:
            _add(getattr(call, "name", None))

    additional = getattr(message, "additional_kwargs", None)
    if isinstance(additional, dict):
        for call in list(additional.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            _add(call.get("name"))
            function = call.get("function")
            if isinstance(function, dict):
                _add(function.get("name"))

    _add(getattr(message, "name", None))
    return names


def _extract_tool_names(messages: list[Any]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for name in _extract_tool_names_from_message(message):
            if name not in names:
                names.append(name)
    return names


def _stringify_for_acceptance(value: Any, *, limit: int = 12000) -> str:
    try:
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            parts: list[str] = []
            for key, item in value.items():
                parts.append(f"{key}: {_stringify_for_acceptance(item, limit=2000)}")
            text = "\n".join(parts)
        elif isinstance(value, (list, tuple, set)):
            text = "\n".join(_stringify_for_acceptance(item, limit=2000) for item in value)
        else:
            text = str(value or "")
    except Exception:
        text = str(value or "")
    return text[:limit]


def _branch_requires_skill_artifact_validation(branch: dict[str, Any]) -> bool:
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    if bool(task_brief.get("validateSkillArtifact") or task_brief.get("validate_skill_artifact")):
        return True
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    if bool(context.get("validateSkillArtifact") or context.get("validate_skill_artifact")):
        return True
    task_id = str(
        branch.get("taskBriefId")
        or task_brief.get("taskBriefId")
        or task_brief.get("taskId")
        or context.get("taskId")
        or ""
    ).strip().upper()
    deliverable = str(task_brief.get("deliverableKind") or task_brief.get("deliverable_kind") or "").strip().lower()
    if deliverable == "skill_artifact":
        return True
    blob = "\n".join(
        _stringify_for_acceptance(value)
        for value in (
            task_id,
            branch.get("reason"),
            branch.get("taskGoal"),
            task_brief.get("title"),
            task_brief.get("goal"),
            task_brief.get("acceptanceContract"),
            context.get("artifactAcceptanceGuard"),
            context.get("expectedOutputs"),
        )
    ).lower()
    if "skill.md" not in blob and "skill artifact" not in blob and "skill_artifact" not in blob:
        return False
    artifact_stage_markers = (
        "组装",
        "构建",
        "生成",
        "写入",
        "创建完整",
        "质量验证",
        "交付前质量验证",
        "build",
        "assemble",
        "write",
        "validate",
    )
    if task_id in {"TASK-010", "TASK-011"}:
        return True
    return any(marker in blob for marker in artifact_stage_markers)


def _infer_required_skill_artifacts(branch: dict[str, Any], state: dict[str, Any]) -> list[Path]:
    if not _branch_requires_skill_artifact_validation(branch):
        return []
    task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    blob = "\n".join(
        part
        for part in [
            _stringify_for_acceptance(branch.get("reason")),
            _stringify_for_acceptance(branch.get("taskGoal")),
            _stringify_for_acceptance(task_brief),
        ]
        if part.strip()
    )
    if ".agents" not in blob or "skills" not in blob:
        return []
    skill_root_match = re.search(
        r"([A-Za-z]:[\\/][^\r\n\"'<>|]*?\.agents[\\/]skills[\\/][^\s\r\n\"'<>|，。；;]+)",
        blob,
    )
    base_dir: Path | None = None
    if skill_root_match:
        raw_path = skill_root_match.group(1).rstrip(".,，。；;:：")
        base_dir = Path(raw_path)
        if base_dir.name.lower() == "skill.md":
            base_dir = base_dir.parent
    else:
        workspace = str(
            state.get("workspace_path")
            or state.get("workspacePath")
            or (state.get("current_route_context") or {}).get("workspace_path")
            or (state.get("current_route_context") or {}).get("workspacePath")
            or ""
        ).strip()
        slug_match = re.search(r"skill[s]?[\\/](?P<slug>[A-Za-z0-9_.-]+)", blob)
        if workspace and slug_match:
            base_dir = Path(workspace) / ".agents" / "skills" / slug_match.group("slug")
    if not base_dir:
        return []
    required = [base_dir / "SKILL.md"]
    if "huashu-nuwa" in blob or "01-writings" in blob or "references/research" in blob.replace("\\", "/"):
        required.extend(
            [
                base_dir / "references" / "research" / "01-writings.md",
                base_dir / "references" / "research" / "02-conversations.md",
                base_dir / "references" / "research" / "03-expression-dna.md",
                base_dir / "references" / "research" / "04-external-views.md",
                base_dir / "references" / "research" / "05-decisions.md",
                base_dir / "references" / "research" / "06-timeline.md",
            ]
        )
    return required


def _validate_required_skill_artifacts(
    *,
    branch: dict[str, Any],
    state: dict[str, Any],
    delta_messages: list[Any],
) -> dict[str, Any] | None:
    required = _infer_required_skill_artifacts(branch, state)
    if not required:
        return None
    requires_huashu_research = any("references" in str(path).replace("\\", "/") and "research" in str(path).replace("\\", "/") for path in required)
    placeholder_re = re.compile(
        r"(待调研|待补充|待填充|占位|空目录|空模板|placeholder|todo|tbd|无官方设定来源|仅示例|示例内容)",
        re.IGNORECASE,
    )
    required_skill_markers = (
        "心智模型",
        "决策启发式",
        "表达DNA",
        "诚实边界",
        "调研来源",
        "时间线",
    )
    missing: list[str] = []
    sparse: list[str] = []
    observed: list[str] = []
    for path in required:
        try:
            if not path.exists():
                missing.append(str(path))
                continue
            observed.append(str(path))
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore")
                stripped = text.strip()
                # Tiny shells are worse than an explicit blocker for reusable skills.
                if path.name == "SKILL.md":
                    if not stripped.startswith("---"):
                        sparse.append(f"{path} (missing_frontmatter)")
                    min_chars = 4000 if requires_huashu_research else 1000
                    if len(stripped) < min_chars:
                        sparse.append(f"{path} (too_short:{len(stripped)}<{min_chars})")
                    if requires_huashu_research:
                        missing_markers = [marker for marker in required_skill_markers if marker not in stripped]
                        if missing_markers:
                            sparse.append(f"{path} (missing_sections:{','.join(missing_markers)})")
                else:
                    min_chars = 500 if requires_huashu_research else 120
                    if len(stripped) < min_chars:
                        sparse.append(f"{path} (too_short:{len(stripped)}<{min_chars})")
                    if requires_huashu_research and not re.search(r"https?://|来源|source|官方|HoYo|米哈游|可信|confidence", stripped, re.IGNORECASE):
                        sparse.append(f"{path} (missing_sources)")
                if placeholder_re.search(stripped):
                    sparse.append(str(path))
        except Exception:
            missing.append(str(path))
    if not missing and not sparse:
        return None
    transcript = _compact_transcript(delta_messages, limit=1200)
    return {
        "status": "failed",
        "error": "artifact_acceptance_failed",
        "dispatchStatus": "artifact_missing_or_sparse",
        "missingArtifacts": missing,
        "sparseArtifacts": sparse,
        "observedArtifacts": observed,
        "localSelfCheck": "Subagent returned before producing required workspace skill artifacts.",
        "acceptanceHint": (
            "Retry after the research handoff is available; write the required SKILL.md and references before reporting success."
        ),
        "compactTranscript": transcript,
    }


def _child_request_from_send_state(
    child_state: dict[str, Any],
    *,
    source_branch: dict[str, Any],
    source_agent_id: str,
    seed: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(child_state, dict):
        return None
    child_branch = dict(child_state.get("parallel_branch") or {})
    if not child_branch:
        return None
    seed = dict(seed or {})
    child_invocation_id = str(child_branch.get("invocationId") or seed.get("childInvocationId") or "").strip()
    child_delegation_id = str(child_branch.get("delegationId") or seed.get("childDelegationId") or "").strip()
    request_id = str(seed.get("requestId") or "").strip()
    if not request_id:
        stable_part = child_invocation_id or child_delegation_id
        request_id = f"child_{stable_part}" if stable_part else f"child_{uuid.uuid4().hex[:12]}"
    return {
        "requestId": request_id,
        "createdAt": seed.get("createdAt") or _now_iso(),
        "sourceInvocationId": seed.get("sourceInvocationId") or source_branch.get("invocationId"),
        "sourceDelegationId": seed.get("sourceDelegationId") or source_branch.get("delegationId"),
        "sourceAgentId": seed.get("sourceAgentId") or source_agent_id,
        "sourceAgentName": seed.get("sourceAgentName") or source_branch.get("agentName") or source_agent_id,
        "sourceAllowChildDelegation": bool(source_branch.get("allowChildDelegation")),
        "sourceChildDelegationBudget": dict(source_branch.get("childDelegationBudget") or {}),
        "childInvocationId": child_invocation_id or seed.get("childInvocationId"),
        "childDelegationId": child_delegation_id or seed.get("childDelegationId"),
        "childTaskBriefId": seed.get("childTaskBriefId") or child_branch.get("taskBriefId"),
        "childTaskGoal": seed.get("childTaskGoal") or child_branch.get("reason"),
        "childAgentId": seed.get("childAgentId") or child_branch.get("agentId"),
        "childAgentName": seed.get("childAgentName") or child_branch.get("agentName"),
        "childDepth": seed.get("childDepth") or child_branch.get("delegationDepth"),
        "send": {
            "node": "parallel_delegate_task",
            "arg": child_state,
        },
    }


def _child_requests_from_pending_records(
    pending: Any,
    *,
    source_branch: dict[str, Any],
    source_agent_id: str,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for raw in list(pending or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        send_data = item.get("send") if isinstance(item.get("send"), dict) else {}
        node = str(send_data.get("node") or item.get("node") or "").strip()
        arg = send_data.get("arg") if isinstance(send_data.get("arg"), dict) else item.get("arg")
        if not isinstance(arg, dict):
            child_branch = item.get("childBranch") if isinstance(item.get("childBranch"), dict) else {}
            if child_branch:
                arg = {"parallel_branch": dict(child_branch)}
        if node and node != "parallel_delegate_task":
            continue
        request = _child_request_from_send_state(
            arg,
            source_branch=source_branch,
            source_agent_id=source_agent_id,
            seed=item,
        ) if isinstance(arg, dict) else None
        if request:
            requests.append(request)
    return requests


def _dedupe_child_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in requests:
        key = str(
            item.get("requestId")
            or item.get("childDelegationId")
            or item.get("childInvocationId")
            or ""
        ).strip()
        if not key:
            key = uuid.uuid4().hex
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _extract_child_delegation_requests(
    goto: Any,
    *,
    source_branch: dict[str, Any],
    source_agent_id: str,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    if isinstance(goto, list):
        items = goto
    elif isinstance(goto, (Command, Send)):
        items = [goto]
    elif isinstance(goto, dict):
        items = [goto]
    else:
        items = []
    for item in items:
        if isinstance(item, Command):
            update = getattr(item, "update", None)
            if isinstance(update, dict):
                requests.extend(
                    _child_requests_from_pending_records(
                        update.get("pending_child_delegations"),
                        source_branch=source_branch,
                        source_agent_id=source_agent_id,
                    )
                )
            requests.extend(
                _extract_child_delegation_requests(
                    getattr(item, "goto", None),
                    source_branch=source_branch,
                    source_agent_id=source_agent_id,
                )
            )
            continue
        if isinstance(item, dict):
            requests.extend(
                _child_requests_from_pending_records(
                    item.get("pending_child_delegations")
                    or (item.get("update") or {}).get("pending_child_delegations")
                    if isinstance(item.get("update"), dict)
                    else item.get("pending_child_delegations"),
                    source_branch=source_branch,
                    source_agent_id=source_agent_id,
                )
            )
            if "goto" in item:
                requests.extend(
                    _extract_child_delegation_requests(
                        item.get("goto"),
                        source_branch=source_branch,
                        source_agent_id=source_agent_id,
                    )
                )
            maybe_node = str(item.get("node") or "").strip()
            maybe_arg = item.get("arg")
            if maybe_node == "parallel_delegate_task" and isinstance(maybe_arg, dict):
                request = _child_request_from_send_state(
                    maybe_arg,
                    source_branch=source_branch,
                    source_agent_id=source_agent_id,
                )
                if request:
                    requests.append(request)
            continue
        if not isinstance(item, Send):
            continue
        if str(getattr(item, "node", "") or "") != "parallel_delegate_task":
            continue
        child_state = getattr(item, "arg", None)
        request = _child_request_from_send_state(
            child_state,
            source_branch=source_branch,
            source_agent_id=source_agent_id,
        )
        if request:
            requests.append(request)
    return _dedupe_child_requests(requests)


def _child_delegation_block_reason(branch: dict[str, Any], child_requests: list[dict[str, Any]]) -> str | None:
    if not child_requests:
        return None
    if not bool(branch.get("allowChildDelegation")):
        return "child_delegation_not_allowed"
    budget = branch.get("childDelegationBudget") if isinstance(branch.get("childDelegationBudget"), dict) else {}
    max_depth = budget.get("maxDepth")
    if max_depth is not None:
        try:
            current_depth = int(branch.get("delegationDepth") or 0)
            if current_depth > int(max_depth):
                return "child_delegation_depth_exceeded"
        except Exception:
            pass
    max_children = budget.get("maxChildren")
    if max_children is not None:
        try:
            if len(child_requests) > int(max_children):
                return "child_delegation_children_exceeded"
        except Exception:
            pass
    return None


def _child_delegation_block_summary(
    *,
    branch: dict[str, Any],
    agent_id: str,
    child_requests: list[dict[str, Any]],
    reason: str,
    delta_messages: list[Any],
    delta_todos: list[Any],
    tool_mode: Any,
) -> dict[str, Any]:
    return {
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
        "status": "blocked",
        "error": reason,
        "dispatchStatus": "dispatch_missing_child_budget" if reason == "child_delegation_not_allowed" else reason,
        "blockedChildDelegationCount": len(child_requests),
        "childDelegationCount": 0,
        "childDelegationRequestIds": [],
        "completedAt": _now_iso(),
        "messageCount": len(delta_messages),
        "todoDeltaCount": len(delta_todos),
        "toolMode": tool_mode,
        "toolsUsed": _extract_tool_names(delta_messages),
        "compactTranscript": _compact_transcript(delta_messages),
        "localSelfCheck": (
            "Subagent requested child delegation, but this branch did not have explicit child delegation budget. "
            "The nested dispatch was blocked to avoid recursive branch explosion."
        ),
        "acceptanceHint": "Route a new delegation episode with explicit allowChildDelegation and childDelegationBudget if child work is still required.",
    }


async def _run_parallel_agent_branch(state: dict[str, Any], agent_data: dict[str, Any]) -> tuple[list[Any], list[Any], dict[str, Any], list[dict[str, Any]]]:
    branch = dict(state.get("parallel_branch") or {})
    agent_id = str(branch.get("agentId") or "")
    current_node = agent_id
    local_state = dict(state)
    local_state["messages"] = list(state.get("messages") or [])
    local_state["todos"] = list(state.get("todos") or [])
    initial_message_count = int(branch.get("initialMessageCount") or len(local_state["messages"]))
    initial_todo_count = int(branch.get("initialTodoCount") or len(local_state["todos"]))

    max_steps = 72 if _infer_required_skill_artifacts(branch, local_state) else 36
    for _ in range(max_steps):
        if current_node == agent_id:
            runtime_context = _runtime_context_from_parallel_state(local_state, branch=branch)

            def _invoke_agent_node() -> Any:
                with bind_runtime_context(**runtime_context):
                    return agent_data["node_func"](local_state)

            result = await asyncio.to_thread(_invoke_agent_node)
        elif current_node == f"{agent_id}_tools":
            tool_node = agent_data.get("tool_node_func")
            if tool_node is None:
                raise RuntimeError(f"{agent_id} 没有可用的工具节点。")
            with bind_runtime_context(**_runtime_context_from_parallel_state(local_state, branch=branch)):
                result = await tool_node(local_state)
        elif current_node == f"{agent_id}_reviewer":
            reviewer = agent_data.get("reviewer_func")
            if reviewer is None:
                raise RuntimeError(f"{agent_id} 没有可用的 reviewer 节点。")
            runtime_context = _runtime_context_from_parallel_state(local_state, branch=branch)

            def _invoke_reviewer_node() -> Any:
                with bind_runtime_context(**runtime_context):
                    return reviewer(local_state)

            result = await asyncio.to_thread(_invoke_reviewer_node)
        else:
            raise RuntimeError(f"{agent_id} 进入了未识别的并发分支节点：{current_node}")

        if isinstance(result, list):
            delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
            delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
            child_requests = _extract_child_delegation_requests(
                result,
                source_branch=branch,
                source_agent_id=agent_id,
            )
            nested_count = len([item for item in result if isinstance(item, (Command, Send))])
            block_reason = _child_delegation_block_reason(branch, child_requests)
            if block_reason:
                return delta_messages, delta_todos, _child_delegation_block_summary(
                    branch=branch,
                    agent_id=agent_id,
                    child_requests=child_requests,
                    reason=block_reason,
                    delta_messages=delta_messages,
                    delta_todos=delta_todos,
                    tool_mode=agent_data.get("tool_mode"),
                ), []
            return delta_messages, delta_todos, {
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
                "status": "waiting_child_delegation" if child_requests else "blocked",
                "error": "delegation_child_requested",
                "nestedDispatchCount": nested_count,
                "childDelegationRequestIds": [item.get("requestId") for item in child_requests],
                "childDelegationCount": len(child_requests),
                "completedAt": _now_iso(),
                "messageCount": len(delta_messages),
                "todoDeltaCount": len(delta_todos),
                "toolMode": agent_data.get("tool_mode"),
                "toolsUsed": _extract_tool_names(delta_messages),
                "compactTranscript": _compact_transcript(delta_messages),
                "localSelfCheck": "Subagent requested child delegation. The top-level router must schedule it as a child Runtime episode instead of running nested Send inside this branch.",
                "acceptanceHint": "Route the child delegation through runtime_broker/delegation_broker with explicit child budget; do not assume the child work completed.",
            }, child_requests
        if not isinstance(result, Command):
            raise RuntimeError(f"{agent_id} 并发分支返回了非 Command 结果。")

        local_state = _merge_state_update(local_state, getattr(result, "update", None) or {})
        goto = getattr(result, "goto", None)
        if isinstance(goto, str):
            if goto == "supervisor":
                break
            current_node = goto
            continue
        if isinstance(goto, list):
            delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
            delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
            child_requests = _extract_child_delegation_requests(
                goto,
                source_branch=branch,
                source_agent_id=agent_id,
            )
            if child_requests:
                block_reason = _child_delegation_block_reason(branch, child_requests)
                if block_reason:
                    return delta_messages, delta_todos, _child_delegation_block_summary(
                        branch=branch,
                        agent_id=agent_id,
                        child_requests=child_requests,
                        reason=block_reason,
                        delta_messages=delta_messages,
                        delta_todos=delta_todos,
                        tool_mode=agent_data.get("tool_mode"),
                    ), []
                return delta_messages, delta_todos, {
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
                    "status": "waiting_child_delegation",
                    "error": "delegation_child_requested",
                    "nestedDispatchCount": len(child_requests),
                    "childDelegationRequestIds": [item.get("requestId") for item in child_requests],
                    "childDelegationCount": len(child_requests),
                    "completedAt": _now_iso(),
                    "messageCount": len(delta_messages),
                    "todoDeltaCount": len(delta_todos),
                    "toolMode": agent_data.get("tool_mode"),
                    "toolsUsed": _extract_tool_names(delta_messages),
                    "compactTranscript": _compact_transcript(delta_messages),
                    "localSelfCheck": "Subagent requested child delegation. The top-level router will schedule it as a child Runtime episode instead of running nested Send inside this branch.",
                    "acceptanceHint": "Wait for the child delegation completion event before merging or judging this branch.",
                }, child_requests
        raise RuntimeError(f"{agent_id} 并发分支返回了不支持的 goto 类型。")
    else:
        raise RuntimeError(f"{agent_id} 并发分支超过最大步数限制。")

    delta_messages = list(local_state.get("messages") or [])[initial_message_count:]
    delta_todos = list(local_state.get("todos") or [])[initial_todo_count:]
    artifact_failure = _validate_required_skill_artifacts(
        branch=branch,
        state=local_state,
        delta_messages=delta_messages,
    )
    if artifact_failure:
        return delta_messages, delta_todos, {
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
            "completedAt": _now_iso(),
            "messageCount": len(delta_messages),
            "todoDeltaCount": len(delta_todos),
            "toolMode": agent_data.get("tool_mode"),
            "toolsUsed": _extract_tool_names(delta_messages),
            **artifact_failure,
        }, []
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
        "toolsUsed": _extract_tool_names(delta_messages),
        "compactTranscript": _compact_transcript(delta_messages),
        "localSelfCheck": "Subagent branch completed; supervisor must still accept, retry, or ignore the result.",
        "acceptanceHint": branch.get("acceptanceHint") or "Supervisor must explicitly accept, retry, or ignore this delegated result.",
    }
    return delta_messages, delta_todos, summary, []


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
            delta_messages, delta_todos, summary, child_requests = await _run_parallel_agent_branch(state, agent_data)
            return Command(
                goto="parallel_delegate_join",
                update={
                    "todos": delta_todos,
                    "parallel_results": [summary],
                    **({"pending_child_delegations": child_requests} if child_requests else {}),
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
    def _child_sends_for_invocation(state: dict[str, Any], invocation_id: str) -> tuple[list[Send], list[dict[str, Any]], list[dict[str, Any]]]:
        routed_request_ids = {str(item or "").strip() for item in list(state.get("routed_child_delegation_request_ids") or [])}
        pending = [
            dict(item)
            for item in list(state.get("pending_child_delegations") or [])
            if str(item.get("sourceInvocationId") or "").strip() == invocation_id
            and str(item.get("requestId") or "").strip() not in routed_request_ids
        ]
        sends: list[Send] = []
        invocation_counts: dict[str, int] = {}
        summaries: list[dict[str, Any]] = []
        seen_request_ids: set[str] = set()
        for item in pending:
            request_id = str(item.get("requestId") or "").strip()
            if request_id and request_id in seen_request_ids:
                continue
            if request_id:
                seen_request_ids.add(request_id)
            send_data = item.get("send") if isinstance(item.get("send"), dict) else {}
            node = str(send_data.get("node") or "").strip()
            arg = send_data.get("arg")
            if node != "parallel_delegate_task" or not isinstance(arg, dict):
                continue
            branch = dict(arg.get("parallel_branch") or {})
            child_invocation_id = str(branch.get("invocationId") or item.get("childInvocationId") or "").strip()
            if not child_invocation_id:
                continue
            invocation_counts[child_invocation_id] = invocation_counts.get(child_invocation_id, 0) + 1
            sends.append(Send("parallel_delegate_task", arg))
            summaries.append(
                {
                    "requestId": request_id,
                    "sourceInvocationId": invocation_id,
                    "sourceDelegationId": item.get("sourceDelegationId"),
                    "childInvocationId": child_invocation_id,
                    "childDelegationId": branch.get("delegationId") or item.get("childDelegationId"),
                    "childTaskBriefId": branch.get("taskBriefId") or item.get("childTaskBriefId"),
                    "childTaskGoal": branch.get("reason") or item.get("childTaskGoal"),
                    "childAgentId": branch.get("agentId") or item.get("childAgentId"),
                    "childAgentName": branch.get("agentName") or item.get("childAgentName"),
                    "childDepth": branch.get("delegationDepth") or item.get("childDepth"),
                    "childBranch": branch,
                    "state": "routed",
                    "createdAt": item.get("createdAt") or _now_iso(),
                }
            )
        invocation_records = [
            {
                "invocationId": child_invocation_id,
                "expected": expected,
                "createdAt": _now_iso(),
                "parentInvocationId": invocation_id,
                "source": "child_delegation_router",
            }
            for child_invocation_id, expected in invocation_counts.items()
        ]
        return sends, invocation_records, summaries

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

        child_sends, child_invocations, child_summaries = _child_sends_for_invocation(state, invocation_id)
        if child_sends:
            route_context = dict(state.get("current_route_context") or {})
            session_id = str(state.get("session_id") or state.get("sessionId") or route_context.get("session_id") or route_context.get("sessionId") or "").strip() or None
            run_id = str(state.get("run_id") or state.get("runId") or route_context.get("run_id") or route_context.get("runId") or "").strip() or None
            workspace_path = str(state.get("workspace_path") or state.get("workspacePath") or route_context.get("workspace_path") or route_context.get("workspacePath") or "").strip() or None
            child_episodes: list[dict[str, Any]] = []
            for child_summary in child_summaries:
                child_branch = dict(child_summary.get("childBranch") or {})
                worker_brief = {
                    "id": child_summary.get("childTaskBriefId") or child_summary.get("childInvocationId"),
                    "title": child_summary.get("childTaskGoal") or "child delegation",
                    "brief": child_summary.get("childTaskGoal") or "Continue the requested child delegation.",
                    "agentId": child_summary.get("childAgentId"),
                    "agentName": child_summary.get("childAgentName"),
                    "runtimeAccess": child_branch.get("runtimeAccess") or ["delegation.recursive"],
                    "parentDelegationId": child_summary.get("sourceDelegationId"),
                    "parentInvocationId": invocation_id,
                    "writeSet": child_branch.get("writeSet"),
                    "acceptanceHint": child_branch.get("acceptanceHint"),
                }
                if workspace_path:
                    worker_brief.setdefault("workspacePath", workspace_path)
                episode = build_runtime_episode(
                    need={
                        "kind": "delegation",
                        "source": "subagent",
                        "reason": child_summary.get("childTaskGoal") or "child delegation",
                        "needId": child_summary.get("childDelegationId") or child_summary.get("childInvocationId"),
                        "parentEpisodeId": child_summary.get("sourceDelegationId") or invocation_id,
                        "inputs": {
                            "targetCount": 1,
                            "workerBriefs": [worker_brief],
                            "allowChildDelegation": bool(child_branch.get("allowChildDelegation")),
                            "childDelegationBudget": child_branch.get("childDelegationBudget") or {},
                            "writeSetPartitions": child_branch.get("writeSetPartitions") or [],
                            **({"workspacePath": workspace_path} if workspace_path else {}),
                        },
                    },
                    kind="delegation",
                    state="queued",
                    required_runtime_access=["delegation.recursive"],
                    parent_episode_id=str(child_summary.get("sourceDelegationId") or invocation_id or ""),
                    continuation_target="runtime_episode_runner",
                    extra={
                        "sourceInvocationId": invocation_id,
                        "childInvocationId": child_summary.get("childInvocationId"),
                        "childTaskBriefId": child_summary.get("childTaskBriefId"),
                        "childAgentId": child_summary.get("childAgentId"),
                        "childAgentName": child_summary.get("childAgentName"),
                        "childDepth": child_summary.get("childDepth"),
                        **({"workspacePath": workspace_path} if workspace_path else {}),
                    },
                )
                with bind_runtime_context(
                    session_id=session_id,
                    run_id=run_id,
                    workspace_path=workspace_path,
                    runtime_kind="delegation",
                    trigger_source="child_delegation_router",
                ):
                    queued_episode = enqueue_runtime_episode(episode, session_id=session_id, run_id=run_id, priority=45)
                route_context = upsert_runtime_episode(route_context, queued_episode)
                child_episodes.append(queued_episode)
                with bind_runtime_context(
                    session_id=session_id,
                    run_id=run_id,
                    workspace_path=workspace_path,
                    runtime_kind="delegation",
                    trigger_source="child_delegation_router",
                ):
                    emit_runtime_episode_event("delegation.child.requested", {"episode": queued_episode, "childDelegation": child_summary})
                    emit_runtime_episode_event("runtime.episode.queued", {"episode": queued_episode})
            return Command(
                goto="supervisor",
                update={
                    "routed_child_delegation_request_ids": [
                        *list(state.get("routed_child_delegation_request_ids") or []),
                        *[str(item.get("requestId") or "") for item in child_summaries if item.get("requestId")],
                    ],
                    "current_route_context": merge_route_context(
                        route_context,
                        {
                            "lastChildDelegationRouted": {
                                "parentInvocationId": invocation_id,
                                "childCount": len(child_sends),
                                "childDelegations": child_summaries[-10:],
                                "childEpisodeIds": [item.get("episodeId") for item in child_episodes],
                                "routedAt": _now_iso(),
                            }
                        },
                    ),
                },
            )

        failures = [item for item in results if item.get("status") != "ok"]
        route_context = dict(state.get("current_route_context") or {})
        handoff_refs: list[dict[str, Any]] = []
        for item in results:
            producer_episode_id = str(item.get("delegationId") or item.get("invocationId") or invocation_id or "").strip()
            compact = str(item.get("compactTranscript") or item.get("localSelfCheck") or item.get("error") or item.get("taskGoal") or "").strip()
            handoff = build_handoff_ref(
                producer_episode_id=producer_episode_id,
                kind="subagent_result",
                status="failed" if item.get("status") != "ok" else "ready",
                compact_summary=compact or f"Subagent result for {producer_episode_id or invocation_id}",
                detail_tool="delegation_broker(mode='observe')",
                consumer_hint=str(item.get("acceptanceHint") or "Supervisor should accept, retry, or ignore this delegated result."),
                extra={
                    "invocationId": item.get("invocationId"),
                    "taskBriefId": item.get("taskBriefId"),
                    "agentId": item.get("agentId"),
                    "agentName": item.get("agentName"),
                    "delegationId": item.get("delegationId"),
                    "toolsUsed": list(item.get("toolsUsed") or item.get("toolNames") or []),
                    "compactTranscript": compact,
                },
            )
            route_context = append_handoff_ref(route_context, handoff)
            route_context, episode = transition_runtime_episode(
                route_context,
                producer_episode_id,
                state="completed" if item.get("status") == "ok" else "failed",
                resultRef=handoff.get("handoffRefId"),
            )
            handoff_refs.append(handoff)
            emit_runtime_episode_event("handoff.ref.created", {"handoffRef": handoff})
            if episode:
                emit_runtime_episode_event(
                    "runtime.episode.completed" if item.get("status") == "ok" else "runtime.episode.failed",
                    {"episode": episode, "handoffRef": handoff},
                )
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
        return Command(
            goto="supervisor",
            update={
                "messages": [summary],
                "current_route_context": merge_route_context(
                    route_context,
                    {
                        "lastDelegationHandoff": {
                            "invocationId": invocation_id,
                            "handoffRefs": [item.get("handoffRefId") for item in handoff_refs],
                            "completedAt": _now_iso(),
                        }
                    },
                ),
            },
        )

    return parallel_delegate_join
