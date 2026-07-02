from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, Send

from core.context.delegation import build_delegation_context, latest_delegation_context
from core.delegation_broker import (
    build_workset_dispatch_decisions,
    choose_best_external_worker_with_diagnostics,
    choose_best_local_agent_with_diagnostics,
    default_external_worker_descriptors,
    external_worker_command_profile,
    expand_delegation_task_briefs,
    make_external_delegation_id,
    make_local_delegation_id,
    normalize_external_worker_descriptors,
    normalize_task_brief,
    normalize_task_briefs,
    parse_delegation_id,
    parse_external_worker_result_block,
    render_external_worker_command,
    reveal_subagent_family,
    task_brief_query_text,
    task_brief_summary,
)
from core.tools.native.command import command_session_broker
from core.runtime_episodes import build_runtime_episode, emit_runtime_episode_event, upsert_runtime_episode
from core.storage import StorageManager
from core.time_truth import utc_now_iso
from erc.runtime_context import get_runtime_context

storage = StorageManager()


def _compat_native_attr(name: str, fallback: Any) -> Any:
    native_tools = sys.modules.get("core.native_tools")
    if native_tools is not None and hasattr(native_tools, name):
        return getattr(native_tools, name)
    return fallback


def _delegation_storage() -> Any:
    return _compat_native_attr("storage", storage)


def _delegation_command_session_broker() -> Any:
    return _compat_native_attr("command_session_broker", command_session_broker)


# --- Background Command Manager ---
def _delegation_broker_payload(
    *,
    mode: str,
    summary: str,
    items: list[dict[str, Any]] | None = None,
    recommended_next_action: str = "none",
    ok: bool = True,
    error: str | None = None,
    **extra: Any,
) -> str:
    payload: dict[str, Any] = {
        "ok": ok,
        "mode": mode,
        "summary": summary,
        "items": list(items or []),
        "recommendedNextAction": recommended_next_action,
    }
    payload.update(extra)
    if error:
        payload["error"] = error
    return json.dumps(
        {key: value for key, value in payload.items() if value not in (None, "", [], {})},
        ensure_ascii=False,
    )


def _delegation_external_worker_descriptors() -> list[dict[str, Any]]:
    supervisor_config = _delegation_storage().get_supervisor_config() or {}
    delegation = dict(supervisor_config.get("delegation") or {})
    descriptors = normalize_external_worker_descriptors(delegation.get("externalWorkers"))
    return descriptors or default_external_worker_descriptors()


def _safe_int_range(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _planner_task_briefs_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    state = dict(state or {})
    planner_plan = state.get("planner_plan")
    briefs: list[Any] = []
    if isinstance(planner_plan, dict):
        for key in ("workerBriefs", "worker_briefs", "taskBriefs", "task_briefs", "tasks"):
            value = planner_plan.get(key)
            if isinstance(value, list) and value:
                briefs = value
                break
    if not briefs:
        route_context = dict(state.get("current_route_context") or {})
        for episode in list(route_context.get("capabilityEpisodes") or []):
            if not isinstance(episode, dict):
                continue
            inputs = episode.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for key in ("workerBriefs", "worker_briefs", "taskBriefs", "task_briefs", "tasks"):
                value = inputs.get(key)
                if isinstance(value, list) and value:
                    briefs = value
                    break
            if briefs:
                break
    return normalize_task_briefs(briefs)


def _minimal_route_task_from_need(need: dict[str, Any], kind: str) -> dict[str, Any]:
    inputs = dict(need.get("inputs") or {}) if isinstance(need.get("inputs"), dict) else {}
    blocked_tool = str(need.get("tool") or inputs.get("blockedTool") or "").strip()
    args = dict(inputs.get("blockedToolArgs") or {}) if isinstance(inputs.get("blockedToolArgs"), dict) else {}
    command = str(args.get("command") or args.get("_raw") or "").strip()
    target_path = str(args.get("path") or args.get("filePath") or args.get("file_path") or "").strip()
    reason = str(need.get("reason") or inputs.get("brief") or inputs.get("query") or "").strip()
    goal = (
        command
        or target_path
        or reason
        or (f"Handle blocked Supervisor tool {blocked_tool} through {kind} runtime." if blocked_tool else f"Run {kind} runtime episode.")
    )
    brief = {
        "taskBriefId": f"route-{kind}-minimal",
        "title": goal[:96],
        "goal": goal,
        "brief": goal,
        "familyHint": "engineering" if kind == "engineering" else ("research" if kind == "research" else "generalist"),
        "executionLaneHint": "auto",
        "requiredCapabilities": ["workspace_mutation", "verification"] if kind == "engineering" else [],
        "acceptanceContract": "Return a compact handoff with outcome, evidence, and next steps.",
    }
    workspace = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip()
    if workspace:
        brief["workspacePath"] = workspace
        brief["writeSet"] = [target_path or workspace]
    if blocked_tool:
        brief["context"] = {"blockedTool": blocked_tool, **({"workspacePath": workspace} if workspace else {})}
    return brief


def _delegation_recursive_policy() -> dict[str, Any]:
    supervisor_config = _delegation_storage().get_supervisor_config() or {}
    delegation = dict(supervisor_config.get("delegation") or {})
    recursive = dict(delegation.get("recursive") or {})
    return {
        "enabled": bool(recursive.get("enabled", True)),
        "maxDelegationDepth": _safe_int_range(recursive.get("maxDelegationDepth"), 10, 1, 100),
        "maxChildrenPerDelegation": _safe_int_range(recursive.get("maxChildrenPerDelegation"), 10, 1, 50),
        "maxTotalDelegationNodes": _safe_int_range(recursive.get("maxTotalDelegationNodes"), 100, 1, 1000),
        "maxConcurrentDelegations": _safe_int_range(recursive.get("maxConcurrentDelegations"), 10, 1, 50),
    }


def _delegation_budget_block_payload(
    *,
    reason: str,
    policy: dict[str, Any],
    depth: int,
    requested_count: int,
    used_nodes: int,
    tool_call_id: str,
) -> Command:
    max_children = int(policy.get("maxChildrenPerDelegation") or 0)
    max_concurrent = int(policy.get("maxConcurrentDelegations") or 0)
    max_total = int(policy.get("maxTotalDelegationNodes") or 0)
    max_depth = int(policy.get("maxDelegationDepth") or 0)
    if reason == "max_children_per_delegation_exceeded":
        summary = (
            f"delegation_broker 已拦截：上层请求派发 {requested_count} 个子任务，"
            f"当前单次递归派发上限为 {max_children} 个。这个值是预算上限，不会自动拉满。"
        )
    elif reason == "max_concurrent_delegations_exceeded":
        summary = (
            f"delegation_broker 已拦截：上层请求并发派发 {requested_count} 个任务，"
            f"当前并发委派上限为 {max_concurrent} 个。"
        )
    elif reason == "max_total_delegation_nodes_exceeded":
        summary = (
            f"delegation_broker 已拦截：任务树已使用 {used_nodes} 个节点，"
            f"本次再派发 {requested_count} 个会超过总节点上限 {max_total} 个。"
        )
    elif reason == "max_delegation_depth_exceeded":
        summary = (
            f"delegation_broker 已拦截：当前递归深度 {depth} 已达到最大深度 {max_depth}。"
        )
    elif reason == "recursive_delegation_disabled":
        summary = "delegation_broker 已拦截：Subagent 递归委派当前已关闭。"
    else:
        summary = "delegation_broker 已拦截：已超过 Subagent 递归预算或策略限制。"

    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode="dispatch",
                        ok=False,
                        summary=summary,
                        recommended_next_action=(
                            "减少本次 tasks 数量，或到 Admin/Subagents 调高递归委派预算后重试。"
                        ),
                        error="delegation_budget_exceeded",
                        reason=reason,
                        budget={
                            "depth": depth,
                            "requestedTaskCount": requested_count,
                            "usedNodeCount": used_nodes,
                            **policy,
                        },
                    ),
                    tool_call_id=tool_call_id,
                )
            ]
        },
    )


def _with_recursive_delegation_access(task_brief: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(task_brief or {})
    runtime_access = [
        str(item).strip()
        for item in list(normalized.get("runtimeAccess") or normalized.get("runtime_access") or [])
        if str(item).strip()
    ]
    policy = _delegation_policy_from_task(normalized)
    allow_child_delegation = bool(
        normalized.get("allowChildDelegation")
        or normalized.get("allow_child_delegation")
        or normalized.get("childDelegationBudget")
        or policy.get("allowChildDelegation")
        or policy.get("allow_child_delegation")
        or policy.get("childDelegationBudget")
    )
    if allow_child_delegation and "delegation.recursive" not in runtime_access:
        runtime_access.append("delegation.recursive")
    normalized["runtimeAccess"] = runtime_access
    return normalized


def _delegation_policy_from_task(task_brief: dict[str, Any]) -> dict[str, Any]:
    policy = task_brief.get("delegationPolicy")
    if isinstance(policy, dict):
        return dict(policy)
    policy = task_brief.get("delegation_policy")
    return dict(policy) if isinstance(policy, dict) else {}


def _delegation_acceptance_hint(value: Any = None) -> str:
    normalized = str(value or "").strip()
    return normalized or "Supervisor must explicitly accept, retry, or ignore this delegated result."


def _delegation_trace_ref(*, run_id: str | None, invocation_id: str | None, branch_index: int | None = None, command_id: str | None = None) -> dict[str, Any]:
    trace: dict[str, Any] = {}
    if str(run_id or "").strip():
        trace["runId"] = str(run_id).strip()
    if str(invocation_id or "").strip():
        trace["invocationId"] = str(invocation_id).strip()
    if branch_index is not None:
        trace["branchIndex"] = int(branch_index)
    if str(command_id or "").strip():
        trace["commandId"] = str(command_id).strip()
    return trace


def _delegation_planner_context(plan: Any, task_brief: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(plan, dict) or not plan:
        return None
    task_id = str(task_brief.get("taskBriefId") or "").strip()
    dependency_rows: list[dict[str, Any]] = []
    for row in list(plan.get("dependencies") or []):
        if not isinstance(row, dict):
            continue
        if task_id and str(row.get("taskBriefId") or "").strip() not in {"", task_id}:
            continue
        dependency_rows.append(
            {
                "taskBriefId": str(row.get("taskBriefId") or "").strip(),
                "dependsOn": [
                    str(item).strip()
                    for item in list(row.get("dependsOn") or row.get("dependency") or [])
                    if str(item).strip()
                ],
            }
        )
    return {
        "planId": str(plan.get("planId") or "").strip(),
        "executionStrategy": str(plan.get("executionStrategy") or "").strip(),
        "planSummary": str(plan.get("planSummary") or "").strip(),
        "globalAcceptanceContract": plan.get("globalAcceptanceContract")
        if isinstance(plan.get("globalAcceptanceContract"), dict)
        else str(plan.get("globalAcceptanceContract") or "").strip(),
        "riskFlags": [
            str(item).strip()
            for item in list(plan.get("riskFlags") or [])
            if str(item).strip()
        ],
        "dependencies": dependency_rows,
        "taskCount": len(list(plan.get("taskBriefs") or [])),
    }


def _delegation_compact_item(
    *,
    delegation_id: str,
    task_brief: dict[str, Any],
    lane: str,
    target_id: str,
    target_label: str,
    status: str,
    trace_ref: dict[str, Any] | None = None,
    artifact_refs: list[Any] | None = None,
    local_self_check: str | None = None,
    acceptance_hint: str | None = None,
    worker_type: str | None = None,
    command_session: dict[str, Any] | None = None,
    worker_result: dict[str, Any] | None = None,
    result_schema_matched: bool | None = None,
    selection_reason: str | None = None,
    selection_confidence: float | None = None,
    match_signals: list[Any] | None = None,
    compat_source: str | None = None,
    supervisor_acceptance: dict[str, Any] | None = None,
    adopted_artifact_refs: list[Any] | None = None,
    auto_dispatch_source: str | None = None,
    invocation_id: str | None = None,
    branch_index: int | None = None,
    workset_dispatch_decision: dict[str, Any] | None = None,
    workset_conflict_group: list[Any] | None = None,
    engineering_capsule_attached: bool | None = None,
    dispatch_blocked_reason: str | None = None,
    repair_suggestion: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "delegationId": delegation_id,
        "taskBriefId": str(task_brief.get("taskBriefId") or "").strip(),
        "taskGoal": str(task_brief.get("goal") or "").strip(),
        "writeSet": [str(item).strip() for item in list(task_brief.get("writeSet") or []) if str(item).strip()],
        "readSet": [str(item).strip() for item in list(task_brief.get("readSet") or []) if str(item).strip()],
        "lane": lane,
        "targetId": target_id,
        "targetLabel": target_label,
        "agentId": target_id,
        "agentName": target_label,
        "status": status,
        "traceRef": trace_ref or {},
        "artifactRefs": list(artifact_refs or []),
        "localSelfCheck": local_self_check,
        "acceptanceHint": _delegation_acceptance_hint(acceptance_hint),
        "supervisorAcceptance": supervisor_acceptance or {
            "status": "pending",
            "summary": "Supervisor has not accepted, retried, or ignored this delegated result yet.",
        },
        "adoptedArtifactRefs": list(adopted_artifact_refs or []),
        "selectionReason": selection_reason,
        "selectionConfidence": selection_confidence,
        "matchSignals": list(match_signals or []),
        "compatSource": compat_source,
        "autoDispatchSource": auto_dispatch_source,
        "invocationId": invocation_id,
        "branchIndex": branch_index,
    }
    if workset_dispatch_decision:
        decision = dict(workset_dispatch_decision)
        decision.setdefault("delegationId", delegation_id)
        decision.setdefault("taskBriefId", item.get("taskBriefId"))
        item["worksetDispatchDecision"] = decision
        item["worksetConflictGroup"] = list(workset_conflict_group or decision.get("worksetConflictGroup") or [])
        item["dispatchBlockedReason"] = dispatch_blocked_reason or (
            str(decision.get("reason") or "").strip()
            if bool(decision.get("blocked"))
            else None
        )
        item["repairSuggestion"] = repair_suggestion or str(decision.get("repairSuggestion") or "").strip() or None
    if engineering_capsule_attached is not None:
        item["engineeringCapsuleAttached"] = bool(engineering_capsule_attached)
    if isinstance(task_brief.get("engineeringTaskCapsule"), dict) and task_brief.get("engineeringTaskCapsule"):
        item["engineeringTaskCapsule"] = task_brief.get("engineeringTaskCapsule")
    if worker_type:
        item["workerType"] = worker_type
    if command_session:
        item["commandSession"] = command_session
    if worker_result:
        item["workerResult"] = {
            key: value
            for key, value in dict(worker_result).items()
            if key in {"status", "summary", "changedFiles", "commandsRun", "verification", "notes"}
            and value not in (None, "", [], {})
        }
    if result_schema_matched is not None:
        item["resultSchemaMatched"] = bool(result_schema_matched)
    if error:
        item["error"] = error
    return {key: value for key, value in item.items() if value not in (None, "", [], {})}


def _normalize_external_worker_result_paths(worker_result: dict[str, Any] | None, *, workspace_path: str = "") -> dict[str, Any] | None:
    if not isinstance(worker_result, dict):
        return None
    normalized = dict(worker_result)
    workspace = Path(str(workspace_path or "")).resolve() if str(workspace_path or "").strip() else None

    def _relative_path(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if workspace:
            try:
                path = Path(text)
                resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
                return str(resolved.relative_to(workspace)).replace("\\", "/")
            except Exception:
                pass
        return text.replace("\\", "/")

    for key in ("changedFiles", "artifactRefs"):
        if isinstance(normalized.get(key), list):
            normalized[key] = [
                item if isinstance(item, dict) else _relative_path(item)
                for item in list(normalized.get(key) or [])
                if item not in (None, "")
            ]
    return normalized


def _external_worker_status_from_result(worker_result: dict[str, Any] | None, *, fallback: str) -> str:
    if not isinstance(worker_result, dict):
        return fallback
    status = str(worker_result.get("status") or "succeeded").strip().lower() or "succeeded"
    if status in {"success", "ok", "done", "complete", "completed"}:
        return "succeeded"
    if status in {"fail", "error"}:
        return "failed"
    return status




def _coerce_delegation_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return value


def _coerce_delegation_list(value: Any, *, nested_keys: tuple[str, ...] = ("tasks", "workerBriefs", "worker_briefs")) -> list[Any]:
    parsed = _coerce_delegation_json_value(value)
    if isinstance(parsed, list):
        return list(parsed)
    if isinstance(parsed, dict):
        for key in nested_keys:
            nested = parsed.get(key)
            if isinstance(nested, list):
                return list(nested)
        return [parsed]
    return []


def _coerce_delegation_dict(value: Any) -> dict[str, Any]:
    parsed = _coerce_delegation_json_value(value)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _delegation_task_has_meaningful_content(value: Any) -> bool:
    parsed = _coerce_delegation_json_value(value)
    if isinstance(parsed, str):
        return bool(parsed.strip())
    if not isinstance(parsed, dict):
        return False
    text_keys = (
        "goal",
        "title",
        "brief",
        "task",
        "description",
        "instructions",
        "prompt",
        "routeQuery",
        "route_query",
        "acceptanceContract",
        "acceptance_contract",
    )
    for key in text_keys:
        if str(parsed.get(key) or "").strip():
            return True
    context = parsed.get("context")
    if isinstance(context, dict) and any(str(item or "").strip() for item in context.values()):
        return True
    if isinstance(context, str) and context.strip():
        return True
    list_keys = (
        "workerBriefs",
        "worker_briefs",
        "workers",
        "branches",
        "parallelBranches",
        "parallel_branches",
        "writeSet",
        "write_set",
        "readSet",
        "read_set",
        "requiredCapabilities",
        "required_capabilities",
        "runtimeAccess",
        "runtime_access",
        "researchRefs",
        "research_refs",
    )
    for key in list_keys:
        raw = parsed.get(key)
        if isinstance(raw, (list, tuple)) and any(_delegation_task_has_meaningful_content(item) or str(item or "").strip() for item in raw):
            return True
    return False


def _filter_meaningful_delegation_tasks(values: list[Any]) -> list[Any]:
    return [item for item in values if _delegation_task_has_meaningful_content(item)]


def _delegation_has_ready_spec_execution_context(context: dict[str, Any]) -> bool:
    for episode in list((context or {}).get("capabilityEpisodes") or []):
        if not isinstance(episode, dict):
            continue
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), dict) else {}
        for payload in (inputs.get("specExecutionBundle"), episode.get("specExecutionBundle")):
            if isinstance(payload, dict) and str(payload.get("status") or "").strip().lower() == "ready":
                return True
    return False


def _delegation_tasks_are_generic_spec_routes(tasks: list[Any]) -> bool:
    normalized = normalize_task_briefs(tasks)
    if not normalized:
        return False
    for task in normalized:
        task_id = str(task.get("taskBriefId") or "").strip().lower()
        blob = " ".join(
            [
                task_id,
                str(task.get("goal") or ""),
                str(task.get("context") or ""),
                str(task.get("routeQuery") or ""),
            ]
        ).lower()
        if task_id.startswith("route-") or "execute approved spec" in blob or "approved_spec_runtime_execution" in blob:
            continue
        return False
    return True


def _delegation_missing_spec_tasks_command(*, tool_call_id: str, source: str) -> Command:
    runtime_context = get_runtime_context()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "unknown").strip() or "unknown"
    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode="dispatch",
                        ok=False,
                        summary=(
                            "delegation_broker 拒绝把已审批 Spec 执行降级成泛化子任务。"
                            "请先由 runtime_broker/Spec 执行分发层提供具体 taskBriefs/workerBriefs。"
                        ),
                        recommended_next_action=(
                            "Call runtime_broker(mode='route', need={'kind':'engineering','specId':'<current specId>'}) "
                            "or dispatch with explicit tasks copied from the approved tasks.md."
                        ),
                        error="spec_delegation_missing_tasks",
                        dispatchStatus="missing_tasks",
                        missingTasks=True,
                        diagnosticKey="delegation_missing_tasks",
                        dispatchGroup=f"delegation_missing_tasks:{run_id}",
                        autoDispatchSource=source,
                    ),
                    tool_call_id=tool_call_id,
                )
            ]
        },
    )


@tool
def delegation_broker(
    mode: str = "observe",
    family: str = "",
    tasks: Any = None,
    target_count: int | None = None,
    worker_briefs: Any = None,
    allow_child_delegation: bool = False,
    child_delegation_budget: Any = None,
    write_set_partitions: Any = None,
    delegation_id: str = "",
    followup: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Dispatch, observe, resume, or interrupt real subagent/external-worker tasks.

    Use this when independent specialist work is actually needed: parallel research, review, writing, implementation planning, or worker handoff. It is not a decorative "Agent Swarm" card. Do not tell ordinary users "delegation_broker"; tell users you are using 子代理/协作 worker.
    Use `mode='reveal'` to inspect a family, then `mode='dispatch'` with explicit tasks/worker_briefs. Each task must include: goal, useful context, expected output, acceptance criteria, constraints/boundaries, workspace/spec/evidence/detailRefs, and any allowed child-delegation budget. Do not dispatch vague ID-only tasks.
    Runtime-bound Research and Creative Media subagents receive their registered tools automatically after dispatch; do not call runtime_broker just to grant those groups. Custom subagents without bindings stay on baseline tools unless the task explicitly grants more.
    Subagents may request child work only through their brokered path when `allow_child_delegation` and budget/briefs allow it; otherwise keep child/sun-agent work as explicit top-level tasks.
    Use `mode='observe'` or `mode='resume'` to collect results, degraded handoffs, or recovery hints before you synthesize a final answer. Supervisor still verifies and merges the result.
    """
    normalized_mode = str(mode or "observe").strip().lower()
    if normalized_mode not in {"reveal", "dispatch", "observe", "resume", "interrupt"}:
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode or "unknown",
                            ok=False,
                            summary=f"Unsupported delegation_broker mode: {normalized_mode}",
                            recommended_next_action="none",
                            error="unsupported_mode",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    base_state = dict(state or {})
    base_messages = list(base_state.get("messages") or [])
    base_todos = list(base_state.get("todos") or [])
    base_contexts = list(base_state.get("delegation_contexts") or [])
    planner_plan = dict(base_state.get("planner_plan") or {}) if isinstance(base_state.get("planner_plan"), dict) else {}
    inherited_context = dict(base_state.get("current_route_context") or {})
    if not inherited_context:
        inherited_context = latest_delegation_context(base_contexts, agent_id=None)
    tasks_list = _coerce_delegation_list(tasks, nested_keys=("tasks", "taskBriefs", "task_briefs"))
    worker_briefs_list = _coerce_delegation_list(worker_briefs, nested_keys=("workerBriefs", "worker_briefs", "workers"))
    child_delegation_budget = _coerce_delegation_dict(child_delegation_budget)
    write_set_partitions_list = _coerce_delegation_list(write_set_partitions, nested_keys=("writeSetPartitions", "write_set_partitions"))

    if normalized_mode == "reveal":
        loaded_agents = _delegation_storage().get_all_agents()
        reveal_payload = reveal_subagent_family(family, loaded_agents)
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode,
                            ok=bool(reveal_payload.get("found")),
                            summary=(
                                f"Family '{str(family or '').strip()}' has {int(reveal_payload.get('memberCount') or 0)} enabled member(s)."
                                if reveal_payload.get("found")
                                else f"No enabled subagents found for family '{str(family or '').strip()}'."
                            ),
                            items=list(reveal_payload.get("members") or []),
                            recommended_next_action="dispatch" if reveal_payload.get("found") else "none",
                            family=str(reveal_payload.get("family") or "").strip(),
                            suggestedRequiredCapabilities=list(reveal_payload.get("suggestedRequiredCapabilities") or []),
                            selectionRule=str(reveal_payload.get("selectionRule") or ""),
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    if normalized_mode == "dispatch":
        requested_tasks = _filter_meaningful_delegation_tasks(list(tasks_list or worker_briefs_list or []))
        dispatch_task_source = "explicit"
        if not requested_tasks:
            requested_tasks = _filter_meaningful_delegation_tasks(_planner_task_briefs_from_state(
                {
                    **base_state,
                    "current_route_context": inherited_context,
                }
            ))
            if requested_tasks:
                dispatch_task_source = "planner_or_episode_fallback"
        if not requested_tasks:
            need_reason = str(inherited_context.get("reason") or inherited_context.get("lastNeedReason") or followup or "").strip()
            active_episodes = [
                item for item in list(inherited_context.get("capabilityEpisodes") or [])
                if isinstance(item, dict) and str(item.get("kind") or "").strip() in {"delegation", "engineering"}
            ]
            if active_episodes:
                latest_episode = active_episodes[-1]
                inputs = latest_episode.get("inputs") if isinstance(latest_episode.get("inputs"), dict) else {}
                need_reason = need_reason or str(latest_episode.get("reason") or inputs.get("brief") or "").strip()
                requested_tasks = [normalize_task_brief(_minimal_route_task_from_need(latest_episode, str(latest_episode.get("kind") or "delegation")))]
                dispatch_task_source = "active_episode_minimal"
            elif need_reason:
                requested_tasks = [normalize_task_brief({"title": need_reason[:96], "goal": need_reason, "brief": need_reason, "executionLaneHint": "auto"})]
                dispatch_task_source = "followup_minimal"
        if (
            dispatch_task_source != "explicit"
            and _delegation_has_ready_spec_execution_context(inherited_context)
            and (not requested_tasks or _delegation_tasks_are_generic_spec_routes(requested_tasks))
        ):
            return _delegation_missing_spec_tasks_command(tool_call_id=tool_call_id, source=dispatch_task_source)
        if target_count and target_count > len(requested_tasks) and requested_tasks:
            seed = dict(requested_tasks[-1])
            for index in range(len(requested_tasks), int(target_count)):
                requested_tasks.append({**seed, "title": f"{seed.get('title') or 'Delegated task'} #{index + 1}"})
        macro_tasks = normalize_task_briefs(requested_tasks)
        normalized_tasks = expand_delegation_task_briefs(requested_tasks)
        if allow_child_delegation or child_delegation_budget or write_set_partitions_list:
            for task in normalized_tasks:
                task.setdefault("delegationPolicy", {})
                task["delegationPolicy"].update(
                    {
                        "allowChildDelegation": bool(allow_child_delegation),
                        **({"childDelegationBudget": child_delegation_budget} if child_delegation_budget else {}),
                        **({"writeSetPartitions": write_set_partitions_list} if write_set_partitions_list else {}),
                    }
                )
        if not normalized_tasks:
            runtime_context = get_runtime_context()
            run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "unknown").strip() or "unknown"
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="delegation_broker(mode=dispatch) 需要提供 tasks。",
                                recommended_next_action="none",
                                error="missing_tasks",
                                dispatchStatus="missing_tasks",
                                missingTasks=True,
                                missingResult=True,
                                diagnosticKey="delegation_missing_tasks",
                                dispatchGroup=f"delegation_missing_tasks:{run_id}",
                                exampleTasks=[
                                    {
                                        "title": "Implement one isolated work package",
                                        "goal": "Describe the exact subtask and expected artifact.",
                                        "runtimeAccess": ["memory.read"],
                                        "acceptanceContract": "Return result summary, touched files, proof, and risks.",
                                    }
                                ],
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )

        recursive_policy = _delegation_recursive_policy()
        parent_delegation_id = str(inherited_context.get("delegationId") or inherited_context.get("parentDelegationId") or "").strip()
        current_depth = _safe_int_range(inherited_context.get("delegationDepth"), 0, 0, 100)
        used_node_count = _safe_int_range(inherited_context.get("delegationNodeCount"), 0, 0, 1000)
        is_recursive_dispatch = bool(parent_delegation_id or current_depth > 0)
        macro_task_count = len(macro_tasks)
        requested_count = len(normalized_tasks)
        if is_recursive_dispatch and not recursive_policy["enabled"]:
            return _delegation_budget_block_payload(
                reason="recursive_delegation_disabled",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
            )
        if is_recursive_dispatch and current_depth >= int(recursive_policy["maxDelegationDepth"]):
            return _delegation_budget_block_payload(
                reason="max_delegation_depth_reached",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
            )
        if is_recursive_dispatch and requested_count > int(recursive_policy["maxChildrenPerDelegation"]):
            return _delegation_budget_block_payload(
                reason="max_children_per_delegation_exceeded",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
            )
        if requested_count > int(recursive_policy["maxConcurrentDelegations"]):
            return _delegation_budget_block_payload(
                reason="max_concurrent_delegations_exceeded",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
            )
        if is_recursive_dispatch and used_node_count + requested_count > int(recursive_policy["maxTotalDelegationNodes"]):
            return _delegation_budget_block_payload(
                reason="max_total_delegation_nodes_exceeded",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
            )

        invocation_id = f"delegation_{uuid.uuid4().hex[:12]}"
        loaded_agents = _delegation_storage().get_all_agents()
        external_descriptors = _delegation_external_worker_descriptors()
        dispatch_source = str(base_state.get("delegationDispatchSource") or inherited_context.get("delegationDispatchSource") or "").strip()
        compat_source = str(base_state.get("delegationCompatSource") or inherited_context.get("delegationCompatSource") or "").strip()
        auto_dispatch_source = dispatch_source if dispatch_source.startswith("planner_auto") else ""
        if dispatch_task_source != "explicit" and not dispatch_source:
            dispatch_source = dispatch_task_source
        workset_decisions = build_workset_dispatch_decisions(
            normalized_tasks,
            auto_dispatch=bool(auto_dispatch_source),
            decision_source="planner_auto" if auto_dispatch_source else "supervisor_manual",
        )
        blocked_decisions = [item for item in workset_decisions if bool(item.get("blocked"))]
        if blocked_decisions:
            blocked_items: list[dict[str, Any]] = []
            for index, task_brief in enumerate(normalized_tasks):
                decision = workset_decisions[index] if index < len(workset_decisions) else {}
                lane_hint = str(task_brief.get("executionLaneHint") or "auto").strip().lower() or "auto"
                blocked_items.append(
                    _delegation_compact_item(
                        delegation_id=f"blocked::workset::{str(task_brief.get('taskBriefId') or index)}::{lane_hint}",
                        task_brief=task_brief,
                        lane="external_worker" if lane_hint == "external_worker" else "subagent",
                        target_id=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or "unassigned").strip() or "unassigned",
                        target_label=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or "unassigned").strip() or "unassigned",
                        status="blocked",
                        invocation_id=invocation_id,
                        branch_index=index,
                        trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                        compat_source=compat_source or None,
                        auto_dispatch_source=auto_dispatch_source or None,
                        workset_dispatch_decision=decision,
                        workset_conflict_group=list(decision.get("worksetConflictGroup") or []),
                        engineering_capsule_attached=bool(decision.get("engineeringCapsuleAttached")),
                        dispatch_blocked_reason=str(decision.get("reason") or "workset_dispatch_blocked").strip(),
                        repair_suggestion=str(decision.get("repairSuggestion") or "Repair planner writeSet before automatic dispatch.").strip(),
                        error="workset_dispatch_blocked",
                    )
                )
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=(
                                    "delegation_broker blocked planner auto-dispatch because Engineering Runtime "
                                    "work-set governance found missing or conflicting write sets."
                                ),
                                items=blocked_items,
                                recommended_next_action="repair_plan",
                                error="workset_dispatch_blocked",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )
        sends: list[Send] = []
        items: list[dict[str, Any]] = []
        parallel_results: list[dict[str, Any]] = []

        for index, task_brief in enumerate(normalized_tasks):
            workset_decision = workset_decisions[index] if index < len(workset_decisions) else {}
            task_query = task_brief_query_text(task_brief) or str(task_brief.get("goal") or "").strip()
            task_goal = str(task_brief.get("goal") or "").strip() or task_query or f"Task {index + 1}"
            lane_hint = str(task_brief.get("executionLaneHint") or "auto").strip().lower() or "auto"
            local_agent = None
            local_diagnostics: dict[str, Any] = {}
            external_diagnostics: dict[str, Any] = {}
            if lane_hint in {"subagent", "auto"}:
                local_agent, local_diagnostics = choose_best_local_agent_with_diagnostics(task_brief, loaded_agents)
            external_worker = None
            if lane_hint == "external_worker":
                external_worker, external_diagnostics = choose_best_external_worker_with_diagnostics(task_brief, external_descriptors)
            elif lane_hint == "auto" and local_agent is None:
                external_worker, external_diagnostics = choose_best_external_worker_with_diagnostics(task_brief, external_descriptors)

            if local_agent and lane_hint != "external_worker":
                agent_id = str(local_agent.get("id") or "").strip()
                agent_name = str(local_agent.get("name") or agent_id).strip() or agent_id
                branch_task_brief = _with_recursive_delegation_access(task_brief)
                delegation_id_value = make_local_delegation_id(
                    invocation_id=invocation_id,
                    branch_index=index,
                    task_brief_id=str(branch_task_brief.get("taskBriefId") or ""),
                    agent_id=agent_id,
                )
                branch_context = build_delegation_context(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    query=task_query,
                    mode="parallel" if len(normalized_tasks) > 1 else "serial",
                    source_runtime_kind=inherited_context.get("sourceRuntimeKind"),
                    selected_skill_ids=inherited_context.get("selectedSkillIds"),
                    selected_skill_names=inherited_context.get("selectedSkillNames"),
                    selected_skill_entries=inherited_context.get("selectedSkillEntries"),
                    skill_root_descriptors=inherited_context.get("skillRootDescriptors"),
                    selected_mcp_tools=inherited_context.get("selectedMcpTools"),
                    selected_plugin_host_tools=inherited_context.get("selectedPluginHostTools"),
                    selected_baseline_tools=inherited_context.get("selectedBaselineTools"),
                    prompt_addition=inherited_context.get("promptAddition"),
                    invocation_id=invocation_id,
                    task_brief=branch_task_brief,
                    planner_context=_delegation_planner_context(planner_plan, branch_task_brief),
                )
                branch_context.update(
                    {
                        "parentDelegationId": parent_delegation_id or None,
                        "delegationId": delegation_id_value,
                        "delegationDepth": current_depth + 1,
                        "delegationNodeCount": used_node_count + requested_count,
                        "delegationBudget": dict(recursive_policy),
                    }
                )
                branch_state = dict(base_state)
                branch_state["messages"] = base_messages + [
                    HumanMessage(content=f"[Supervisor Delegated Task to {agent_name}]:\n{task_query or task_goal}")
                ]
                branch_state["todos"] = list(base_todos)
                branch_state["delegation_contexts"] = base_contexts + [branch_context]
                branch_state["current_route_context"] = branch_context
                delegation_policy = _delegation_policy_from_task(branch_task_brief)
                branch_state["parallel_branch"] = {
                    "invocationId": invocation_id,
                    "branchIndex": index,
                    "agentId": agent_id,
                    "agentName": agent_name,
                    "reason": task_goal,
                    "taskBriefId": str(branch_task_brief.get("taskBriefId") or f"{invocation_id}:{index}").strip(),
                    "taskBrief": branch_task_brief,
                    "delegationId": delegation_id_value,
                    "parentDelegationId": parent_delegation_id or None,
                    "delegationDepth": current_depth + 1,
                    "lane": "subagent",
                    "acceptanceHint": _delegation_acceptance_hint(branch_task_brief.get("acceptanceContract")),
                    "allowChildDelegation": bool(delegation_policy.get("allowChildDelegation")),
                    "childDelegationBudget": dict(delegation_policy.get("childDelegationBudget") or {}),
                    "writeSetPartitions": list(delegation_policy.get("writeSetPartitions") or []),
                    "initialMessageCount": len(base_messages) + 1,
                    "initialTodoCount": len(base_todos),
                }
                sends.append(Send("parallel_delegate_task", branch_state))
                items.append(
                    _delegation_compact_item(
                        delegation_id=delegation_id_value,
                        task_brief=task_brief,
                        lane="subagent",
                        target_id=agent_id,
                        target_label=agent_name,
                        status="queued",
                        invocation_id=invocation_id,
                        branch_index=index,
                        trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                        selection_reason=str(local_diagnostics.get("selectionReason") or "").strip() or None,
                        selection_confidence=local_diagnostics.get("selectionConfidence"),
                        match_signals=list(local_diagnostics.get("matchSignals") or []),
                        compat_source=compat_source or None,
                        auto_dispatch_source=auto_dispatch_source or None,
                        workset_dispatch_decision=workset_decision,
                        workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                        engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                        repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                    )
                )
                continue

            if external_worker:
                rendered_command = render_external_worker_command(
                    descriptor=external_worker,
                    task_brief=task_brief,
                    workspace_path=str(base_state.get("workspace_path") or ""),
                )
                if not rendered_command:
                    item = _delegation_compact_item(
                        delegation_id=make_external_delegation_id(
                            command_id=f"missing-command-{index}",
                            task_brief_id=str(task_brief.get("taskBriefId") or ""),
                            worker_id=str(external_worker.get("id") or ""),
                        ),
                        task_brief=task_brief,
                        lane="external_worker",
                        target_id=str(external_worker.get("id") or ""),
                        target_label=str(external_worker.get("name") or external_worker.get("id") or "external-worker").strip(),
                        status="error",
                        invocation_id=invocation_id,
                        branch_index=index,
                        worker_type=str(external_worker.get("workerType") or "").strip() or None,
                        trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                        selection_reason=str(external_diagnostics.get("selectionReason") or "").strip() or None,
                        selection_confidence=external_diagnostics.get("selectionConfidence"),
                        match_signals=list(external_diagnostics.get("matchSignals") or []),
                        compat_source=compat_source or None,
                        auto_dispatch_source=auto_dispatch_source or None,
                        workset_dispatch_decision=workset_decision,
                        workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                        engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                        repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                        error="missing_command_template",
                    )
                    items.append(item)
                    parallel_results.append(item)
                    continue

                raw_start_payload = _delegation_command_session_broker().func(
                    mode="start",
                    command=rendered_command,
                    profile=external_worker_command_profile(external_worker),
                    tool_call_id=tool_call_id,
                )
                start_payload = json.loads(str(raw_start_payload or "{}"))
                command_id = str(start_payload.get("commandId") or start_payload.get("sessionId") or "").strip()
                worker_result = parse_external_worker_result_block(
                    start_payload.get("workerResultBlock") or start_payload.get("semanticTextTail") or start_payload.get("initialPreview"),
                    markers=((external_worker.get("resultSchema") or {}).get("markers") or []),
                )
                worker_result = _normalize_external_worker_result_paths(
                    worker_result,
                    workspace_path=str(base_state.get("workspace_path") or ""),
                )
                worker_status = str(start_payload.get("state") or "running").strip() or "running"
                if worker_result:
                    worker_status = _external_worker_status_from_result(worker_result, fallback="succeeded")
                elif worker_status in {"completed", "failed"}:
                    worker_status = "marker_missing"
                delegation_id_value = make_external_delegation_id(
                    command_id=command_id or f"pending-{uuid.uuid4().hex[:8]}",
                    task_brief_id=str(task_brief.get("taskBriefId") or ""),
                    worker_id=str(external_worker.get("id") or ""),
                )
                worker_item = _delegation_compact_item(
                    delegation_id=delegation_id_value,
                    task_brief=task_brief,
                    lane="external_worker",
                    target_id=str(external_worker.get("id") or ""),
                    target_label=str(external_worker.get("name") or external_worker.get("id") or "external-worker").strip(),
                    status=worker_status,
                    invocation_id=invocation_id,
                    branch_index=index,
                    worker_type=str(external_worker.get("workerType") or "").strip() or None,
                    command_session={
                        "commandId": command_id,
                        "sessionId": str(start_payload.get("sessionId") or command_id).strip() or command_id,
                        "runId": start_payload.get("runId"),
                        "profile": start_payload.get("profile"),
                        "workerResultDetected": bool(start_payload.get("workerResultDetected")),
                    },
                    trace_ref=_delegation_trace_ref(
                        run_id=start_payload.get("runId") or base_state.get("run_id"),
                        invocation_id=invocation_id,
                        branch_index=index,
                        command_id=command_id,
                    ),
                    local_self_check=str((worker_result or {}).get("localSelfCheck") or "").strip() or None,
                    artifact_refs=list((worker_result or {}).get("artifactRefs") or []),
                    acceptance_hint=(worker_result or {}).get("acceptanceHint"),
                    worker_result=worker_result,
                    result_schema_matched=bool(worker_result),
                    selection_reason=str(external_diagnostics.get("selectionReason") or "").strip() or None,
                    selection_confidence=external_diagnostics.get("selectionConfidence"),
                    match_signals=list(external_diagnostics.get("matchSignals") or []),
                    compat_source=compat_source or None,
                    auto_dispatch_source=auto_dispatch_source or None,
                    workset_dispatch_decision=workset_decision,
                    workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                    engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                    repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                    error=None if bool(start_payload.get("ok", True)) else str(start_payload.get("error") or "external_worker_start_failed"),
                )
                items.append(worker_item)
                parallel_results.append(worker_item)
                continue

            unresolved_lane = "external_worker" if lane_hint == "external_worker" else "subagent"
            item = _delegation_compact_item(
                delegation_id=f"{unresolved_lane}::unresolved::{str(task_brief.get('taskBriefId') or index)}::{lane_hint}",
                task_brief=task_brief,
                lane=unresolved_lane,
                target_id=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or lane_hint).strip() or unresolved_lane,
                target_label=str(task_brief.get("preferredAgentId") or task_brief.get("preferredWorkerType") or lane_hint).strip() or unresolved_lane,
                status="error",
                invocation_id=invocation_id,
                branch_index=index,
                trace_ref=_delegation_trace_ref(run_id=base_state.get("run_id"), invocation_id=invocation_id, branch_index=index),
                selection_reason=str((external_diagnostics or local_diagnostics).get("selectionReason") or "no_matching_target").strip(),
                selection_confidence=(external_diagnostics or local_diagnostics).get("selectionConfidence", 0.0),
                match_signals=list((external_diagnostics or local_diagnostics).get("matchSignals") or []),
                compat_source=compat_source or None,
                auto_dispatch_source=auto_dispatch_source or None,
                workset_dispatch_decision=workset_decision,
                workset_conflict_group=list(workset_decision.get("worksetConflictGroup") or []),
                engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                repair_suggestion=str(workset_decision.get("repairSuggestion") or "").strip() or None,
                error="no_matching_target",
            )
            items.append(item)
            parallel_results.append(item)

        summary = f"Delegation broker queued {len(items)} task(s): " + ", ".join(
            task_brief_summary(task_brief) or f"task-{index + 1}"
            for index, task_brief in enumerate(normalized_tasks)
        )
        if requested_count != macro_task_count:
            summary = f"Delegation broker expanded {macro_task_count} macro task(s) into {requested_count} worker task(s): " + ", ".join(
                task_brief_summary(task_brief) or f"task-{index + 1}"
                for index, task_brief in enumerate(normalized_tasks)
            )
        update: dict[str, Any] = {
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode=normalized_mode,
                        summary=summary,
                        items=items,
                        macroTaskCount=macro_task_count,
                        requestedTaskCount=requested_count,
                        recommended_next_action="observe" if any(item.get("lane") == "external_worker" for item in items) else "review",
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
        dispatch_route_context = dict(inherited_context or {})
        for item in items:
            if not isinstance(item, dict):
                continue
            delegation_id_value = str(item.get("delegationId") or "").strip()
            if not delegation_id_value:
                continue
            status = str(item.get("status") or "").strip().lower()
            episode_state = "failed" if status in {"error", "blocked", "failed"} else "waiting"
            task_brief_value = item.get("taskBrief") if isinstance(item.get("taskBrief"), dict) else {}
            episode = build_runtime_episode(
                need={
                    "kind": "delegation",
                    "needId": delegation_id_value,
                    "source": "delegation_broker",
                    "reason": str(item.get("taskGoal") or item.get("targetLabel") or "delegated task"),
                    "parentEpisodeId": parent_delegation_id or inherited_context.get("activeCapabilityEpisodeId") or "",
                },
                kind="delegation",
                state=episode_state,
                required_runtime_access=list(task_brief_value.get("runtimeAccess") or []),
                parent_episode_id=parent_delegation_id or inherited_context.get("activeCapabilityEpisodeId") or "",
                continuation_target="parallel_delegate_join" if item.get("lane") == "subagent" else "delegation_broker.observe",
                extra={
                    "invocationId": invocation_id,
                    "taskBriefId": item.get("taskBriefId"),
                    "targetId": item.get("targetId"),
                    "targetLabel": item.get("targetLabel"),
                    "lane": item.get("lane"),
                    "branchIndex": item.get("branchIndex"),
                    "error": item.get("error"),
                },
            )
            dispatch_route_context = upsert_runtime_episode(dispatch_route_context, episode)
            emit_runtime_episode_event("capability.need.detected", {"episode": episode})
            emit_runtime_episode_event(
                "runtime.episode.failed" if episode_state == "failed" else "runtime.episode.waiting",
                {"episode": episode},
            )
        update["current_route_context"] = dispatch_route_context
        if sends:
            update["parallel_invocations"] = [
                {
                    "invocationId": invocation_id,
                    "expected": requested_count,
                    "dispatchedSubagentCount": len(sends),
                    "immediateResultCount": len(parallel_results),
                    "createdAt": utc_now_iso(),
                }
            ]
        if parallel_results:
            update["parallel_results"] = parallel_results
        return Command(goto=sends if sends else "supervisor", update=update)

    parsed = parse_delegation_id(delegation_id)
    if str(parsed.get("lane") or "").strip() != "external_worker":
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode,
                            ok=False,
                            summary="当前 observe/resume/interrupt 仅支持 external_worker delegationId。",
                            recommended_next_action="dispatch",
                            error="unsupported_lane",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            },
        )

    external_descriptors = _delegation_external_worker_descriptors()
    descriptor = next(
        (
            item
            for item in external_descriptors
            if str(item.get("id") or "").strip() == str(parsed.get("targetId") or "").strip()
        ),
        None,
    )
    task_brief = normalize_task_brief({"taskBriefId": str(parsed.get("taskBriefId") or "").strip(), "goal": ""})
    command_id = str(parsed.get("commandId") or "").strip()

    if normalized_mode == "resume":
        followup_text = str(followup or "").strip()
        if not followup_text:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="delegation_broker(mode=resume) 需要提供 followup。",
                                recommended_next_action="none",
                                error="missing_followup",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )
        raw_payload = _delegation_command_session_broker().func(
            mode="input",
            session_id=command_id,
            input_text=followup_text,
            tool_call_id=tool_call_id,
        )
    elif normalized_mode == "interrupt":
        raw_payload = _delegation_command_session_broker().func(
            mode="terminate",
            session_id=command_id,
            tool_call_id=tool_call_id,
        )
    else:
        raw_payload = _delegation_command_session_broker().func(
            mode="observe",
            session_id=command_id,
            tool_call_id=tool_call_id,
        )

    payload = json.loads(str(raw_payload or "{}"))
    markers = ((descriptor or {}).get("resultSchema") or {}).get("markers") or []
    worker_result = parse_external_worker_result_block(
        payload.get("workerResultBlock")
        or payload.get("semanticTextTail")
        or payload.get("deltaText")
        or payload.get("finalPreview")
        or payload.get("initialPreview"),
        markers=markers,
    )
    worker_result = _normalize_external_worker_result_paths(
        worker_result,
        workspace_path=str(base_state.get("workspace_path") or ""),
    )
    worker_status = str(payload.get("state") or ("terminated" if normalized_mode == "interrupt" else "running")).strip() or "running"
    if worker_result:
        worker_status = _external_worker_status_from_result(worker_result, fallback="succeeded")
    elif worker_status in {"completed", "failed"}:
        worker_status = "marker_missing"
    worker_item = _delegation_compact_item(
        delegation_id=delegation_id,
        task_brief=task_brief,
        lane="external_worker",
        target_id=str((descriptor or {}).get("id") or parsed.get("targetId") or "").strip(),
        target_label=str((descriptor or {}).get("name") or parsed.get("targetId") or "external-worker").strip(),
        status=worker_status,
        worker_type=str((descriptor or {}).get("workerType") or "").strip() or None,
        command_session={
            "commandId": command_id,
            "sessionId": str(payload.get("sessionId") or command_id).strip() or command_id,
            "runId": payload.get("runId"),
            "profile": payload.get("profile"),
            "workerResultDetected": bool(payload.get("workerResultDetected")),
        },
        trace_ref=_delegation_trace_ref(
            run_id=payload.get("runId") or base_state.get("run_id"),
            invocation_id=None,
            command_id=command_id,
        ),
        local_self_check=str((worker_result or {}).get("localSelfCheck") or "").strip() or None,
        artifact_refs=list((worker_result or {}).get("artifactRefs") or []),
        acceptance_hint=(worker_result or {}).get("acceptanceHint"),
        worker_result=worker_result,
        result_schema_matched=bool(worker_result),
        error=None if bool(payload.get("ok", True)) else str(payload.get("error") or f"{normalized_mode}_failed"),
    )
    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode=normalized_mode,
                        ok=bool(payload.get("ok", True)),
                        summary=str(payload.get("summary") or f"Delegation {normalized_mode} completed.").strip(),
                        items=[worker_item],
                        recommended_next_action=str(payload.get("recommendedNextAction") or "none").strip() or "none",
                        error=str(payload.get("error") or "").strip() or None,
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "parallel_results": [worker_item],
        },
    )


__all__ = [name for name in globals() if name.startswith("_delegation") or name in {"delegation_broker", "_with_recursive_delegation_access", "_normalize_external_worker_result_paths", "_external_worker_status_from_result", "_coerce_delegation_json_value", "_coerce_delegation_list", "_coerce_delegation_dict", "_delegation_task_has_meaningful_content", "_filter_meaningful_delegation_tasks", "_safe_int_range"}]

