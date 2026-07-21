from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path
from typing import Annotated, Any

from typing_extensions import Required, TypedDict

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, Send

from core.actor_identity import resolve_collaboration_actor
from core.agents import agents_from_subagent_registry_snapshot, build_subagent_registry_snapshot
from core.context.delegation import build_delegation_context, latest_delegation_context
from core.command_environment import default_shell_dialect
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
from core.database import db
from core.engineering_capsule import (
    bind_engineering_task_workspace,
    derive_grandchild_engineering_task,
    engineering_capsule_mode,
)
from core.engineering_sandbox.delegation import prepare_delegated_engineering_workspace
from core.tools.native.command import command_session_broker
from core.runtime_episodes import (
    TERMINAL_EPISODE_STATES,
    build_runtime_episode,
    emit_runtime_episode_event,
    persist_runtime_episode,
    upsert_runtime_episode,
)
from core.storage import StorageManager
from core.time_truth import utc_now_iso
from erc.runtime_context import bind_runtime_context, get_runtime_context

storage = StorageManager()

_EPISODE_REF_RE = re.compile(r"(?:episode_[A-Za-z0-9]+|subagent::[A-Za-z0-9:_-]+)")

_VERIFICATION_TASK_SIGNALS = (
    "risk review",
    "final review",
    "verification",
    "verify the result",
    "validation review",
    "acceptance review",
    "regression review",
    "audit the result",
    "风险复核",
    "最终复核",
    "验收复核",
    "回归复核",
    "验证结果",
    "校验结果",
    "审计结果",
)

_PREFERRED_WORKER_AGENT_ALIASES = {
    "verifier": "verification-engineer",
    "verification": "verification-engineer",
    "verification_engineer": "verification-engineer",
    "verification-engineer": "verification-engineer",
}


_DEPRECATED_DELEGATION_TARGET_IDS = {"project-planner"}


def _is_supervisor_delegation_caller(runtime_context: dict[str, Any]) -> bool:
    runtime_kind = str(runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or "").strip().lower()
    agent_id = str(
        runtime_context.get("agent_id")
        or runtime_context.get("agentId")
        or runtime_context.get("subagent_id")
        or runtime_context.get("subagentId")
        or ""
    ).strip().lower()
    return runtime_kind == "chat" or agent_id == "supervisor"


def _delegation_parent_episode_id(
    inherited_context: dict[str, Any],
    runtime_context: dict[str, Any],
) -> str:
    """Resolve a real recursive parent without inheriting stale Supervisor state."""

    if _is_supervisor_delegation_caller(runtime_context):
        return ""

    explicit_parent = str(
        inherited_context.get("parentDelegationId")
        or inherited_context.get("delegationId")
        or ""
    ).strip()
    if explicit_parent:
        return explicit_parent

    active_episode_id = str(inherited_context.get("activeCapabilityEpisodeId") or "").strip()
    if not active_episode_id:
        return ""
    for episode in list(inherited_context.get("capabilityEpisodes") or []):
        if not isinstance(episode, dict):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if episode_id != active_episode_id:
            continue
        state = str(episode.get("state") or episode.get("status") or "").strip().lower()
        return "" if state in TERMINAL_EPISODE_STATES else active_episode_id
    return active_episode_id


def _delegation_root_episode_id(
    inherited_context: dict[str, Any],
    *,
    parent_episode_id: str,
    runtime_owner_episode_id: str,
) -> str:
    """Project the existing tree root instead of making a child its own root."""

    anchor_id = str(parent_episode_id or runtime_owner_episode_id or "").strip()
    if not anchor_id:
        return ""
    runtime_context = get_runtime_context()
    explicit_root_id = str(
        inherited_context.get("rootEpisodeId")
        or inherited_context.get("root_episode_id")
        or runtime_context.get("rootEpisodeId")
        or runtime_context.get("root_episode_id")
        or ""
    ).strip()
    if explicit_root_id:
        return explicit_root_id
    for episode in list(inherited_context.get("capabilityEpisodes") or []):
        if not isinstance(episode, dict):
            continue
        episode_id = str(episode.get("episodeId") or episode.get("id") or "").strip()
        if episode_id != anchor_id:
            continue
        return str(
            episode.get("rootEpisodeId")
            or episode.get("root_episode_id")
            or anchor_id
        ).strip()
    durable_parent = db.get_runtime_episode(anchor_id)
    if durable_parent:
        return str(
            durable_parent.get("rootEpisodeId")
            or durable_parent.get("root_episode_id")
            or anchor_id
        ).strip()
    return anchor_id


def _apply_delegation_target_defaults(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply structured routing hints without overriding explicit model choices."""

    normalized: list[dict[str, Any]] = []
    for task in tasks:
        item = dict(task)
        preferred_agent_id = str(item.get("preferredAgentId") or "").strip()
        if preferred_agent_id.lower() in _DEPRECATED_DELEGATION_TARGET_IDS:
            item.pop("preferredAgentId", None)
            item["targetDefaultReason"] = "deprecated_target_removed"
            preferred_agent_id = ""
        preferred_worker_type = str(item.get("preferredWorkerType") or "").strip().lower().replace(" ", "_")
        aliased_agent_id = _PREFERRED_WORKER_AGENT_ALIASES.get(preferred_worker_type, "")
        if not preferred_agent_id and aliased_agent_id:
            item["preferredAgentId"] = aliased_agent_id
            item["targetDefaultReason"] = "preferred_worker_type_alias"
            preferred_agent_id = aliased_agent_id
        family_hint = str(item.get("familyHint") or "").strip().lower()
        runtime_access = {
            str(value or "").strip().lower()
            for value in list(item.get("runtimeAccess") or item.get("runtime_access") or [])
            if str(value or "").strip()
        }
        specialist_runtime_requested = any(
            value.startswith(("creative_media", "computer_use", "rpa", "research"))
            for value in runtime_access
        )
        tool_policy = item.get("toolPolicy") if isinstance(item.get("toolPolicy"), dict) else {}
        allowed_tools = {
            str(value or "").strip()
            for value in list(tool_policy.get("allowedTools") or item.get("allowedTools") or [])
            if str(value or "").strip()
        }
        structured_workspace_task = bool(
            item.get("engineeringTaskCapsule")
            or item.get("engineering_task_capsule")
            or item.get("readSet")
            or item.get("read_set")
            or item.get("writeSet")
            or item.get("write_set")
            or item.get("criticalFiles")
            or item.get("critical_files")
            or allowed_tools.intersection({"read_native_file", "write_native_file", "grep_search"})
        )
        if (
            not preferred_agent_id
            and not family_hint
            and structured_workspace_task
            and not specialist_runtime_requested
        ):
            item["familyHint"] = "engineering"
            item["targetDefaultReason"] = "structured_workspace_task"
            family_hint = "engineering"
        write_execution = bool(
            engineering_capsule_mode(item) == "write"
            or item.get("writeRequired")
            or item.get("write_required")
            or item.get("writeSet")
            or item.get("write_set")
        )
        if not preferred_agent_id and not write_execution and family_hint in {"", "engineering"}:
            signal = " ".join(
                str(value or "")
                for value in (
                    item.get("title"),
                    item.get("goal"),
                    item.get("expectedOutput"),
                    item.get("expectedOutputs"),
                    item.get("acceptanceContract"),
                    item.get("proofExpectations"),
                )
            ).lower()
            if any(token in signal for token in _VERIFICATION_TASK_SIGNALS):
                item.setdefault("familyHint", "engineering")
                item["preferredAgentId"] = "verification-engineer"
                item["targetDefaultReason"] = "verification_task_signal"
        normalized.append(item)
    return normalized


def _apply_delegation_tool_defaults(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep injected-handoff review work out of unrelated workspace tools."""

    normalized: list[dict[str, Any]] = []
    for task in tasks:
        item = dict(task)
        context = dict(item.get("context") or {}) if isinstance(item.get("context"), dict) else {}
        tool_policy = dict(item.get("toolPolicy") or {}) if isinstance(item.get("toolPolicy"), dict) else {}
        tool_mode = str(tool_policy.get("mode") or "default").strip().lower()
        allowed_tools = list(tool_policy.get("allowedTools") or item.get("allowedTools") or [])
        read_set = [str(value or "").strip() for value in list(item.get("readSet") or []) if str(value or "").strip()]
        write_set = [str(value or "").strip() for value in list(item.get("writeSet") or []) if str(value or "").strip()]
        handoff_evidence = bool(
            context.get("upstreamHandoffs")
            or context.get("dependencyResults")
            or context.get("injectedEvidenceRefs")
            or context.get("handoffUsage")
        )
        read_only = bool(context.get("readOnly") or context.get("noSideEffect") or item.get("readOnly"))
        if (
            handoff_evidence
            and read_only
            and not read_set
            and not write_set
            and not allowed_tools
            and tool_mode in {"", "default", "none"}
        ):
            item["toolPolicy"] = {"mode": "none", "allowedTools": [], "forbiddenTools": []}
            item["allowedTools"] = []
            context.setdefault(
                "handoffConsumptionDiscipline",
                "Review the injected upstreamHandoffs/dependencyResults directly. No filesystem lookup is authorized because this task declares no readSet.",
            )
            item["context"] = context
        normalized.append(item)
    return normalized


def _compact_handoff_values(value: Any, *, limit: int = 8) -> list[Any]:
    values = value if isinstance(value, list) else [value] if value not in (None, "") else []
    result: list[Any] = []
    for item in values:
        if item in result:
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _compact_upstream_handoff_for_agent(handoff: dict[str, Any]) -> dict[str, Any]:
    payload = dict(handoff or {})
    compact: dict[str, Any] = {
        "producerEpisodeId": str(payload.get("producerEpisodeId") or payload.get("episodeId") or "").strip(),
        "handoffRefId": str(payload.get("handoffRefId") or payload.get("handoffId") or "").strip(),
        "kind": str(payload.get("kind") or "runtime_handoff").strip(),
        "status": str(payload.get("status") or "unknown").strip(),
        "summary": str(payload.get("compactSummary") or payload.get("summary") or "").strip()[:6000],
        "confidence": str(payload.get("confidence") or "").strip(),
        "refs": _compact_handoff_values(payload.get("refs") or payload.get("researchRefs")),
        "proofRefs": _compact_handoff_values(payload.get("proofRefs") or payload.get("verificationRefs")),
        "artifactRefs": _compact_handoff_values(payload.get("artifactRefs") or payload.get("changedFiles")),
        "limitations": _compact_handoff_values(payload.get("limitations"), limit=6),
        "consumerHint": str(payload.get("consumerHint") or "").strip()[:800],
        "detailRef": str(payload.get("detailRef") or "").strip(),
    }
    child_handoffs = [
        _compact_upstream_handoff_for_agent(dict(item))
        for item in list(payload.get("childHandoffs") or [])
        if isinstance(item, dict)
    ][:6]
    delegation_handoff = payload.get("delegationHandoff")
    if isinstance(delegation_handoff, dict):
        nested_children = [
            _compact_upstream_handoff_for_agent(dict(item))
            for item in list(delegation_handoff.get("childHandoffs") or [])
            if isinstance(item, dict)
        ][:6]
        child_handoffs.extend(item for item in nested_children if item not in child_handoffs)
    if child_handoffs:
        compact["childResults"] = child_handoffs[:6]
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def _episode_ids_from_task_brief(task_brief: dict[str, Any]) -> set[str]:
    candidates = [
        task_brief.get("evidenceRefs"),
        task_brief.get("detailRefs"),
        task_brief.get("researchRefs"),
        task_brief.get("dependency"),
        task_brief.get("goal"),
        task_brief.get("context"),
    ]
    serialized = json.dumps(candidates, ensure_ascii=False, default=str)
    return set(_EPISODE_REF_RE.findall(serialized))


def _inject_inherited_handoffs_into_tasks(
    tasks: list[dict[str, Any]],
    inherited_context: dict[str, Any],
) -> list[dict[str, Any]]:
    handoffs = [
        dict(item)
        for item in list((inherited_context or {}).get("handoffRefs") or [])
        if isinstance(item, dict)
    ]
    compact_handoffs = [_compact_upstream_handoff_for_agent(item) for item in handoffs]
    result: list[dict[str, Any]] = []
    for task in tasks:
        item = dict(task)
        context_value = item.get("context")
        context = dict(context_value) if isinstance(context_value, dict) else {}
        if isinstance(context_value, str) and context_value.strip():
            context.setdefault("notes", context_value.strip())
        requested_episode_ids = _episode_ids_from_task_brief(item)
        matched = [
            handoff
            for handoff in compact_handoffs
            if not requested_episode_ids
            or str(handoff.get("producerEpisodeId") or "") in requested_episode_ids
        ]
        if requested_episode_ids and not matched:
            matched = compact_handoffs[-4:]
        if matched:
            context["upstreamHandoffs"] = matched[-6:]
            context["handoffUsage"] = (
                "These handoffs are injected evidence, not filesystem paths. Read their summary/childResults directly; "
                "do not search the workspace for research://, engineering://, episode IDs, or invented bundle filenames."
            )
            item["upstreamHandoffRefs"] = [
                str(handoff.get("handoffRefId") or "")
                for handoff in matched
                if str(handoff.get("handoffRefId") or "").strip()
            ]
        context.setdefault("shellDialect", default_shell_dialect())
        item["context"] = context
        result.append(item)
    return result


class DelegationToolPolicyInput(TypedDict, total=False):
    mode: str
    allowedTools: list[str]
    forbiddenTools: list[str]
    noTools: bool


class DelegationTaskInput(TypedDict, total=False):
    taskBriefId: Required[str]
    title: str
    goal: Required[str]
    context: Any
    expectedOutput: str
    expectedOutputs: Required[list[str]]
    acceptanceContract: Required[str | dict[str, Any] | list[Any]]
    acceptanceTiers: Any
    constraints: list[str] | str
    behaviorScope: list[str] | str
    toolPolicy: DelegationToolPolicyInput
    allowedTools: list[str] | str
    forbiddenTools: list[str] | str
    noTools: bool
    requiredCapabilities: list[str] | str
    runtimeAccess: list[str] | str
    readSet: list[str] | str
    writeSet: list[str] | str
    proofExpectations: list[str] | str
    evidenceRefs: list[str] | str
    detailRefs: list[str] | str
    researchRefs: list[str] | str
    pluginReferences: list[dict[str, Any]]
    executionLaneHint: str
    familyHint: str
    targetAgentName: str
    preferredAgentId: str
    preferredWorkerType: str
    dependency: list[str] | str
    allowChildDelegation: bool
    requireChildDelegation: bool
    childDelegationBudget: dict[str, Any]


def _compat_native_attr(name: str, fallback: Any) -> Any:
    native_tools = sys.modules.get("core.native_tools")
    if native_tools is not None and hasattr(native_tools, name):
        return getattr(native_tools, name)
    return fallback


def _delegation_storage() -> Any:
    return _compat_native_attr("storage", storage)


def _delegation_command_session_broker() -> Any:
    return _compat_native_attr("command_session_broker", command_session_broker)


def _registry_snapshot_from_state_or_agents(
    base_state: dict[str, Any],
    loaded_agents: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state_snapshot = base_state.get("subagent_registry_snapshot")
    if isinstance(state_snapshot, dict) and str(state_snapshot.get("schemaVersion") or "") == "v8.subagent_registry_snapshot.v1":
        snapshot_agents = agents_from_subagent_registry_snapshot(state_snapshot)
        if snapshot_agents:
            created_agent_ids: set[str] = set()
            for message in list(base_state.get("messages") or [])[-24:]:
                content = getattr(message, "content", "")
                if isinstance(content, list):
                    content = "\n".join(
                        str(item.get("text") or item.get("content") or "") if isinstance(item, dict) else str(item or "")
                        for item in content
                    )
                try:
                    payload = json.loads(str(content or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                message_name = str(getattr(message, "name", "") or "").strip().lower()
                if message_name != "agent_broker" and str(payload.get("tool") or "").strip().lower() != "agent_broker":
                    continue
                if not (
                    payload.get("ok") is True
                    and str(payload.get("mode") or "").strip().lower() == "create"
                    and str(payload.get("status") or "").strip().lower() == "created"
                ):
                    continue
                item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                created_id = str(item.get("agentId") or item.get("id") or "").strip()
                if created_id:
                    created_agent_ids.add(created_id)

            snapshot_ids = {str(agent.get("id") or "").strip() for agent in snapshot_agents}
            newly_created_agents = [
                dict(agent)
                for agent in loaded_agents
                if isinstance(agent, dict)
                and str(agent.get("id") or "").strip() in created_agent_ids - snapshot_ids
            ]
            if newly_created_agents:
                refreshed = build_subagent_registry_snapshot([*snapshot_agents, *newly_created_agents])
                return refreshed, agents_from_subagent_registry_snapshot(refreshed)
            return dict(state_snapshot), snapshot_agents
    snapshot = build_subagent_registry_snapshot(loaded_agents)
    return snapshot, agents_from_subagent_registry_snapshot(snapshot)


def _registry_version(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("version") or "").strip()


def _registry_hash(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("hash") or "").strip()


def _compact_registered_agent_catalog(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("isEnabled") is False:
            continue
        agent_id = str(agent.get("id") or "").strip()
        if not agent_id or agent_id == "supervisor":
            continue
        snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
        catalog.append(
            {
                "name": str(agent.get("name") or agent_id).strip() or agent_id,
                "description": re.sub(r"\s+", " ", str(agent.get("description") or "").strip())[:240],
                "family": str(snapshot.get("specialistFamily") or snapshot.get("family") or "freelancers").strip(),
            }
        )
    catalog.sort(key=lambda item: (str(item.get("family") or ""), str(item.get("name") or "").casefold()))
    return catalog


def _active_collaborator_summaries(
    tasks: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    *,
    current_index: int,
    mirror_parent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for peer_index, peer_task in enumerate(tasks):
        if peer_index == current_index or not isinstance(peer_task, dict):
            continue
        lane_hint = str(peer_task.get("executionLaneHint") or "auto").strip().lower() or "auto"
        if lane_hint == "external_worker":
            continue
        if mirror_parent:
            parent_name = str(mirror_parent.get("name") or mirror_parent.get("id") or "subagent").strip()
            peer_name = f"{parent_name} · worker-{peer_index + 1:02d}"
        else:
            peer_agent, _diagnostics = choose_best_local_agent_with_diagnostics(peer_task, agents)
            if not peer_agent:
                continue
            peer_name = str(peer_agent.get("name") or peer_agent.get("id") or "subagent").strip()
        summaries.append(
            {
                "name": peer_name,
                "workSummary": str(peer_task.get("goal") or task_brief_summary(peer_task) or "delegated task").strip()[:360],
                "taskBriefId": str(peer_task.get("taskBriefId") or f"task-{peer_index + 1}").strip(),
                "status": "queued_or_active",
            }
        )
    return summaries


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


def _delegation_recursive_policy() -> dict[str, Any]:
    supervisor_config = _delegation_storage().get_supervisor_config() or {}
    delegation = dict(supervisor_config.get("delegation") or {})
    recursive = dict(delegation.get("recursive") or {})
    return {
        "enabled": bool(recursive.get("enabled", True)),
        "maxDelegationDepth": min(2, _safe_int_range(recursive.get("maxDelegationDepth"), 2, 1, 100)),
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
    retry_node: str = "supervisor",
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
        goto=retry_node or "supervisor",
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
    policy_explicit = normalized.get("childDelegationPolicyExplicit")
    allow_child_delegation = policy_explicit is not True or bool(
        normalized.get("allowChildDelegation")
        or normalized.get("allow_child_delegation")
        or policy.get("allowChildDelegation")
        or policy.get("allow_child_delegation")
    )
    if allow_child_delegation and "delegation.recursive" not in runtime_access:
        runtime_access.append("delegation.recursive")
    normalized["runtimeAccess"] = runtime_access
    return normalized


def _terminalize_grandchild_task_brief(task_brief: dict[str, Any]) -> dict[str, Any]:
    """Make the depth-two boundary explicit in both authority and model-visible policy."""

    terminal = dict(task_brief or {})
    terminal["allowChildDelegation"] = False
    terminal["requireChildDelegation"] = False
    terminal["childDelegationPolicyExplicit"] = True
    terminal["childDelegationBudget"] = {}
    terminal["runtimeAccess"] = [
        str(item).strip()
        for item in list(terminal.get("runtimeAccess") or terminal.get("runtime_access") or [])
        if str(item).strip() and str(item).strip() != "delegation.recursive"
    ]
    terminal["allowedTools"] = [
        str(item).strip()
        for item in list(terminal.get("allowedTools") or terminal.get("allowed_tools") or [])
        if str(item).strip() and str(item).strip() != "delegation_broker"
    ]
    tool_policy = dict(terminal.get("toolPolicy") or {}) if isinstance(terminal.get("toolPolicy"), dict) else {}
    if tool_policy:
        tool_policy["allowedTools"] = [
            str(item).strip()
            for item in list(tool_policy.get("allowedTools") or tool_policy.get("allowed_tools") or [])
            if str(item).strip() and str(item).strip() != "delegation_broker"
        ]
        terminal["toolPolicy"] = tool_policy
    delegation_policy = (
        dict(terminal.get("delegationPolicy") or {})
        if isinstance(terminal.get("delegationPolicy"), dict)
        else {}
    )
    if delegation_policy:
        delegation_policy["allowChildDelegation"] = False
        delegation_policy["requireChildDelegation"] = False
        delegation_policy["childDelegationBudget"] = {}
        terminal["delegationPolicy"] = delegation_policy
    return terminal


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
    registry_version: str | None = None,
    registry_hash: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "delegationId": delegation_id,
        "taskBriefId": str(task_brief.get("taskBriefId") or "").strip(),
        "taskGoal": str(task_brief.get("goal") or "").strip(),
        "writeSet": [str(item).strip() for item in list(task_brief.get("writeSet") or []) if str(item).strip()],
        "readSet": [str(item).strip() for item in list(task_brief.get("readSet") or []) if str(item).strip()],
        "expectedOutputs": list(task_brief.get("expectedOutputs") or []),
        "behaviorScope": list(task_brief.get("behaviorScope") or []),
        "toolPolicy": dict(task_brief.get("toolPolicy") or {}) if isinstance(task_brief.get("toolPolicy"), dict) else {},
        "acceptanceContract": task_brief.get("acceptanceContract"),
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
        "registryVersion": registry_version,
        "registryHash": registry_hash,
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


def _finalize_external_worker_workspace(
    *,
    managed_workspace: dict[str, Any] | None,
    worker_status: str,
    run_id: str | None,
    invocation_id: str,
) -> dict[str, Any]:
    managed = dict(managed_workspace or {})
    worktree_id = str(managed.get("worktree_id") or managed.get("worktreeId") or "").strip()
    if not worktree_id:
        return {}
    normalized_status = str(worker_status or "").strip().lower()
    from core.engineering_sandbox.service import get_engineering_sandbox_service

    service = get_engineering_sandbox_service()
    if normalized_status not in {"succeeded", "success", "ok", "completed", "failed", "marker_missing", "terminated"}:
        return {}
    if normalized_status not in {"succeeded", "success", "ok", "completed"}:
        service.mark_task_workspace_failed(
            worktree_id=worktree_id,
            error_code=f"external_worker_{normalized_status or 'failed'}",
        )
        return {
            "sandboxEvidence": {
                "worktreeId": worktree_id,
                "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
                "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
                "state": "failed",
                "errorCode": f"external_worker_{normalized_status or 'failed'}",
            }
        }
    change_set = service.finalize_task_workspace(
        worktree_id=worktree_id,
        commit_message=f"V8OS external worker {invocation_id}",
    )
    parent_merge = service.merge_child_change_set_to_parent(
        child_worktree_id=worktree_id,
        run_id=str(run_id or invocation_id),
    )
    result: dict[str, Any] = {
        "gitChangeSet": change_set.as_dict(),
        "sandboxEvidence": {
            "worktreeId": worktree_id,
            "leaseId": managed.get("sandbox_lease_id") or managed.get("sandboxLeaseId"),
            "policyDigest": managed.get("sandbox_policy_digest") or managed.get("sandboxPolicyDigest"),
            "state": "completed",
        },
    }
    if parent_merge.get("status") == "merged_to_parent":
        result["parentWorktreeMerge"] = parent_merge
    elif run_id:
        integration, integration_change_set = service.build_run_integration(
            run_id=str(run_id),
            invocation_id=worktree_id,
            change_sets=[change_set.as_dict()],
        )
        result["integrationChangeSet"] = integration_change_set.as_dict()
        result["integrationWorktreeId"] = integration.worktree_id
    return result




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
        "pluginReferences",
        "plugin_references",
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


def _coerce_peer_help_capabilities(value: Any) -> list[str]:
    parsed = _coerce_delegation_json_value(value)
    if isinstance(parsed, str):
        raw_items = parsed.split(",")
    elif isinstance(parsed, list):
        raw_items = parsed
    else:
        raw_items = []
    result: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= 12:
            break
    return result


@tool
def request_peer_help(
    needed_capabilities: Annotated[Any, "Required capabilities, as a list or comma-separated text. Do not include a target subagent id."] = None,
    reason: Annotated[str, "Why the current subagent cannot complete this alone."] = "",
    context: Annotated[str, "Brief task context and constraints to pass back to the broker."] = "",
    preferred_family: Annotated[str, "Optional family hint such as research, engineering, creative_media, writing, or freelancers."] = "",
    state: Annotated[dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "request_peer_help",
):
    """Subagent-only handoff request. It reports capability needs; Supervisor/broker chooses the peer."""

    base_state = dict(state or {})
    branch = dict(base_state.get("parallel_branch") or {})
    capabilities = _coerce_peer_help_capabilities(needed_capabilities)
    reason_text = str(reason or "").strip() or "The current subagent needs brokered peer help."
    context_text = str(context or "").strip()
    family_hint = str(preferred_family or "").strip()
    if not bool(branch.get("allowChildDelegation")):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode="request_peer_help",
                            ok=False,
                            summary="Peer help request was blocked because this delegation did not grant child handoff.",
                            recommended_next_action="report_blocker_to_supervisor",
                            error="child_delegation_not_allowed",
                            neededCapabilities=capabilities,
                            reason=reason_text,
                            context=context_text,
                            preferredFamily=family_hint,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    request_id = f"peer_help_{uuid.uuid4().hex[:12]}"
    child_task_brief = normalize_task_brief(
        {
            "taskBriefId": request_id,
            "title": reason_text[:96],
            "goal": reason_text,
            "brief": context_text or reason_text,
            "requiredCapabilities": capabilities,
            **({"familyHint": family_hint} if family_hint else {}),
            "context": {
                "requestKind": "handoff_request",
                "sourceAgentId": branch.get("agentId"),
                "sourceDelegationId": branch.get("delegationId"),
                "sourceTaskBriefId": branch.get("taskBriefId"),
                "reason": reason_text,
                "notes": context_text,
            },
        }
    )
    parent_task_brief = branch.get("taskBrief") if isinstance(branch.get("taskBrief"), dict) else {}
    child_task_brief = derive_grandchild_engineering_task(
        parent_task_brief,
        child_task_brief,
        shell_dialect=default_shell_dialect(),
    )
    child_branch = {
        "invocationId": f"{branch.get('invocationId') or 'peer'}:help:{request_id}",
        "delegationId": f"{branch.get('delegationId') or 'delegation'}:help:{request_id}",
        "taskBriefId": request_id,
        "taskBrief": child_task_brief,
        "reason": reason_text,
        "delegationDepth": _safe_int_range(branch.get("delegationDepth"), 0, 0, 100) + 1,
        "allowChildDelegation": False,
        "childDelegationBudget": {},
        "runtimeAccess": [],
    }
    pending = {
        "requestId": request_id,
        "requestKind": "handoff_request",
        "createdAt": utc_now_iso(),
        "sourceInvocationId": branch.get("invocationId"),
        "sourceDelegationId": branch.get("delegationId"),
        "sourceAgentId": branch.get("agentId"),
        "sourceAgentName": branch.get("agentName"),
        "neededCapabilities": capabilities,
        "preferredFamily": family_hint,
        "reason": reason_text,
        "context": context_text,
        "childTaskBriefId": request_id,
        "childTaskGoal": reason_text,
        "childTaskBrief": child_task_brief,
        "childBranch": child_branch,
    }
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode="request_peer_help",
                        ok=True,
                        summary="Peer help request captured. Supervisor/broker must choose the target subagent.",
                        recommended_next_action="broker_child_delegation",
                        items=[{
                            "requestId": request_id,
                            "neededCapabilities": capabilities,
                            "preferredFamily": family_hint,
                            "reason": reason_text,
                        }],
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "pending_child_delegations": [pending],
        }
    )


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
                            "Route through runtime_broker using its canonical engineering need contract, placing the current "
                            "specId and approved task refs in need.inputs.taskBriefs[].context; or dispatch explicit tasks "
                            "copied from the approved tasks.md."
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


def _grandchild_write_contract_block_payload(
    *,
    task_brief_ids: list[str],
    tool_call_id: str,
    retry_node: str,
) -> Command:
    return Command(
        goto=retry_node or "supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_delegation_broker_payload(
                        mode="dispatch",
                        ok=False,
                        summary=(
                            "孙 Agent 没有继承父任务的写权限。本次任务请求复写父任务范围，"
                            "因此不能把直接子 Agent 自己的实现职责下放到孙代。"
                        ),
                        recommended_next_action=(
                            "直接子 Agent 先在自己的 worktree 完成写入，再只派发只读验证任务；"
                            "只有父合同明确划分的严格子集 writeSet 才能授权孙代写入。"
                        ),
                        error="grandchild_write_authority_not_granted",
                        blockedTaskBriefIds=task_brief_ids,
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
    tasks: Annotated[
        list[DelegationTaskInput] | dict[str, Any] | str | None,
        "Flat task briefs. Minimal dispatch form: tasks=[{taskBriefId, goal, expectedOutputs, acceptanceContract, toolPolicy}]. Never pass tasks={} and never wrap an item inside taskBrief.",
    ] = None,
    target_count: int | None = None,
    worker_briefs: Annotated[
        Any,
        "Read-compatible legacy alias for tasks. New calls must use the typed tasks array; do not send both fields.",
    ] = None,
    allow_child_delegation: bool = False,
    child_delegation_budget: Any = None,
    write_set_partitions: Any = None,
    delegation_id: str = "",
    followup: str = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Dispatch, observe, resume, or interrupt real local subagent/external-worker tasks.

    Use this when independent specialist work is actually needed: parallel research, review, writing, implementation planning, or worker handoff. It is not a decorative "Agent Swarm" card. Do not tell ordinary users "delegation_broker"; tell users you are using 子代理/协作 worker.
    Before a manual Supervisor dispatch, call `agent_broker(mode='list')` or use the exact visible registry, then pass `targetAgentName` for every local task. familyHint is explanatory metadata, not permission to guess a worker. Copy this valid shape and replace values without changing JSON types: `tasks=[{"taskBriefId":"task-1","targetAgentName":"Implementation Engineer","goal":"Implement the requested focused change.","context":{"source":"current user turn"},"expectedOutputs":["Changed file and verification result"],"acceptanceContract":["Requested behavior is present","Focused verification passes"],"constraints":["Stay inside the assigned workspace scope"],"toolPolicy":{"mode":"default"}}]`. Never wrap a task inside `{taskBrief:{...}}`, never send `tasks={}`, and never mix `tasks` with the legacy `worker_briefs` alias. Each task must include: goal, useful context, expected output, acceptance criteria, constraints/boundaries, workspace/spec/evidence/detailRefs, and any allowed child-delegation budget. Do not dispatch vague ID-only tasks. `toolPolicy: {mode: 'default'}` keeps the role's public toolbox so the Agent can choose the smallest relevant subset. Use `mode: 'none'` only for injected-evidence reasoning with no tool work, and use an allowlist only when the task is intentionally closed-world or explicitly restricted; an acceptance contract is not itself a reason to narrow tools.
    Runtime-bound Research and Creative Media subagents receive their registered tools automatically after dispatch; do not call runtime_broker just to grant those groups. Custom subagents without bindings stay on baseline tools unless the task explicitly grants more.
    A direct subagent may use its brokered path for one grandchild by default. The direct subagent must complete its own assigned writes before delegating; the grandchild is normally an independent verifier and never inherits the parent's writeSet. Only an explicitly partitioned strict-subset writeSet may be delegated. Set task `requireChildDelegation=true` when the must-level acceptance contract itself requires that verifier; set `allow_child_delegation=false` to forbid the path, or provide `child_delegation_budget` to narrow the default. Grandchildren remain terminal and cannot delegate again.
    Local subagent results are injected by the graph; never poll them. Use `mode='observe'` or `mode='resume'` only for an explicit external_worker delegationId or one terminal diagnostic read. Supervisor still verifies and merges the result.
    """
    normalized_mode = str(mode or "observe").strip().lower()
    runtime_context = get_runtime_context()
    has_explicit_actor_identity = any(
        runtime_context.get(key) not in (None, "")
        for key in (
            "actor_role",
            "actorRole",
            "runtime_kind",
            "runtimeKind",
            "agent_id",
            "agentId",
            "subagent_id",
            "subagentId",
            "delegation_id",
            "delegationId",
            "delegation_depth",
            "delegationDepth",
        )
    )
    caller = resolve_collaboration_actor(
        actor="supervisor" if not has_explicit_actor_identity else None,
        runtime_context=runtime_context,
    )
    if not caller.is_collaboration_actor or caller.is_grandchild:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode or "unknown",
                            ok=False,
                            summary="当前 actor 不在可继续委派的协作层级。孙 Agent 是委派树的终点。",
                            recommended_next_action="return_evidence_to_parent",
                            error="delegation_depth_terminal",
                            delegationDepth=caller.delegation_depth,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
    if caller.is_direct_subagent and normalized_mode != "dispatch":
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=_delegation_broker_payload(
                            mode=normalized_mode or "unknown",
                            ok=False,
                            summary="直接子 Agent 只可派发一个孙 Agent 层级；目录揭示、外部 worker 观察和控制仍由 Supervisor 负责。",
                            recommended_next_action="dispatch_with_complete_task_brief",
                            error="delegation_mode_not_available_to_subagent",
                            delegationDepth=caller.delegation_depth,
                        ),
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )
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
    inherited_context = dict(base_state.get("current_route_context") or {})
    if not inherited_context:
        inherited_context = latest_delegation_context(base_contexts, agent_id=None)
    tasks_list = _coerce_delegation_list(tasks, nested_keys=("tasks", "taskBriefs", "task_briefs"))
    worker_briefs_list = _coerce_delegation_list(worker_briefs, nested_keys=("workerBriefs", "worker_briefs", "workers"))
    child_delegation_budget = _coerce_delegation_dict(child_delegation_budget)
    write_set_partitions_list = _coerce_delegation_list(write_set_partitions, nested_keys=("writeSetPartitions", "write_set_partitions"))

    if normalized_mode == "reveal":
        loaded_agents = _delegation_storage().get_all_agents()
        registry_snapshot, registry_agents = _registry_snapshot_from_state_or_agents(base_state, loaded_agents)
        reveal_payload = reveal_subagent_family(family, registry_agents)
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
                            registryVersion=_registry_version(registry_snapshot),
                            registryHash=_registry_hash(registry_snapshot),
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
        if target_count and target_count > len(requested_tasks) and requested_tasks:
            seed = dict(requested_tasks[-1])
            for index in range(len(requested_tasks), int(target_count)):
                requested_tasks.append({**seed, "title": f"{seed.get('title') or 'Delegated task'} #{index + 1}"})
        macro_tasks = normalize_task_briefs(requested_tasks)
        normalized_tasks = expand_delegation_task_briefs(requested_tasks)
        normalized_tasks = _apply_delegation_target_defaults(normalized_tasks)
        normalized_tasks = _inject_inherited_handoffs_into_tasks(
            normalized_tasks,
            inherited_context,
        )
        normalized_tasks = _apply_delegation_tool_defaults(normalized_tasks)
        if allow_child_delegation or child_delegation_budget or write_set_partitions_list:
            for task in normalized_tasks:
                task["allowChildDelegation"] = bool(allow_child_delegation)
                task["childDelegationPolicyExplicit"] = True
                if child_delegation_budget:
                    task["childDelegationBudget"] = dict(child_delegation_budget)
                if write_set_partitions_list:
                    task["writeSetPartitions"] = list(write_set_partitions_list)
                task.setdefault("delegationPolicy", {})
                task["delegationPolicy"].update(
                    {
                        "allowChildDelegation": bool(allow_child_delegation),
                        **({"childDelegationBudget": child_delegation_budget} if child_delegation_budget else {}),
                        **({"writeSetPartitions": write_set_partitions_list} if write_set_partitions_list else {}),
                    }
                )
        if not normalized_tasks:
            if _delegation_has_ready_spec_execution_context(inherited_context):
                return _delegation_missing_spec_tasks_command(
                    tool_call_id=tool_call_id,
                    source=dispatch_task_source,
                )
            runtime_context = get_runtime_context()
            run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "unknown").strip() or "unknown"
            retry_node = caller.agent_id if caller.is_direct_subagent and caller.agent_id else "supervisor"
            return Command(
                goto=retry_node,
                update={
                    "messages": [
                        ToolMessage(
                            content=_delegation_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=(
                                    "delegation_broker(mode=dispatch) 需要至少一个完整的扁平 tasks 项。"
                                    "请按 exampleTasks 修正后重试一次。"
                                ),
                                recommended_next_action=(
                                    "retry_dispatch_with_complete_flat_task"
                                    if caller.is_direct_subagent
                                    else "repair_task_contract"
                                ),
                                error="missing_tasks",
                                dispatchStatus="missing_tasks",
                                missingTasks=True,
                                missingResult=True,
                                diagnosticKey="delegation_missing_tasks",
                                dispatchGroup=f"delegation_missing_tasks:{run_id}",
                                exampleTasks=[
                                    {
                                        "taskBriefId": "child-check-1",
                                        "goal": "Independently inspect the assigned evidence and return the requested fact.",
                                        "expectedOutputs": ["result", "evidence", "limitations"],
                                        "acceptanceContract": "Return a compact result with evidence and limitations.",
                                        "toolPolicy": {
                                            "mode": "allowlist",
                                            "allowedTools": ["read_native_file"],
                                        },
                                    }
                                ],
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ]
                },
            )

        recursive_policy = _delegation_recursive_policy()
        supervisor_dispatch = _is_supervisor_delegation_caller(runtime_context)
        parent_delegation_id = _delegation_parent_episode_id(inherited_context, runtime_context)
        current_depth = 0 if supervisor_dispatch else caller.delegation_depth
        used_node_count = 0 if supervisor_dispatch else _safe_int_range(inherited_context.get("delegationNodeCount"), 0, 0, 1000)
        is_recursive_dispatch = bool(parent_delegation_id or current_depth > 0)
        macro_task_count = len(macro_tasks)
        requested_count = len(normalized_tasks)
        recursive_retry_node = (
            str(caller.agent_id or "").strip()
            if caller.is_direct_subagent and str(caller.agent_id or "").strip()
            else "supervisor"
        )
        source_branch = (
            dict(base_state.get("parallel_branch") or {})
            if isinstance(base_state.get("parallel_branch"), dict)
            else {}
        )
        source_child_budget = (
            dict(source_branch.get("childDelegationBudget") or {})
            if isinstance(source_branch.get("childDelegationBudget"), dict)
            else {}
        )
        source_max_children = source_child_budget.get("maxChildren")
        if caller.is_direct_subagent and source_max_children is not None:
            effective_max_children = min(
                int(recursive_policy["maxChildrenPerDelegation"]),
                _safe_int_range(source_max_children, 1, 1, 50),
            )
            if requested_count > effective_max_children:
                return _delegation_budget_block_payload(
                    reason="max_children_per_delegation_exceeded",
                    policy={
                        **recursive_policy,
                        "maxChildrenPerDelegation": effective_max_children,
                    },
                    depth=current_depth,
                    requested_count=requested_count,
                    used_nodes=used_node_count,
                    tool_call_id=tool_call_id,
                    retry_node=recursive_retry_node,
                )
        if caller.is_direct_subagent:
            parent_task_brief = (
                dict(inherited_context.get("taskBrief") or {})
                if isinstance(inherited_context.get("taskBrief"), dict)
                else {}
            )
            denied_write_tasks: list[str] = []
            for index, task in enumerate(normalized_tasks):
                write_intent = bool(
                    engineering_capsule_mode(task) == "write"
                    or task.get("writeRequired")
                    or task.get("write_required")
                    or task.get("writeSet")
                    or task.get("write_set")
                )
                if not write_intent:
                    continue
                derived = (
                    derive_grandchild_engineering_task(
                        parent_task_brief,
                        task,
                        shell_dialect=default_shell_dialect(),
                    )
                    if parent_task_brief
                    else {}
                )
                if not derived or engineering_capsule_mode(derived) != "write":
                    denied_write_tasks.append(
                        str(task.get("taskBriefId") or f"task-{index + 1}").strip()
                    )
            if denied_write_tasks:
                return _grandchild_write_contract_block_payload(
                    task_brief_ids=denied_write_tasks,
                    tool_call_id=tool_call_id,
                    retry_node=recursive_retry_node,
                )
        if is_recursive_dispatch and not recursive_policy["enabled"]:
            return _delegation_budget_block_payload(
                reason="recursive_delegation_disabled",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
                retry_node=recursive_retry_node,
            )
        if is_recursive_dispatch and current_depth >= int(recursive_policy["maxDelegationDepth"]):
            return _delegation_budget_block_payload(
                reason="max_delegation_depth_reached",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
                retry_node=recursive_retry_node,
            )
        if is_recursive_dispatch and requested_count > int(recursive_policy["maxChildrenPerDelegation"]):
            return _delegation_budget_block_payload(
                reason="max_children_per_delegation_exceeded",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
                retry_node=recursive_retry_node,
            )
        if requested_count > int(recursive_policy["maxConcurrentDelegations"]):
            return _delegation_budget_block_payload(
                reason="max_concurrent_delegations_exceeded",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
                retry_node=recursive_retry_node,
            )
        if is_recursive_dispatch and used_node_count + requested_count > int(recursive_policy["maxTotalDelegationNodes"]):
            return _delegation_budget_block_payload(
                reason="max_total_delegation_nodes_exceeded",
                policy=recursive_policy,
                depth=current_depth,
                requested_count=requested_count,
                used_nodes=used_node_count,
                tool_call_id=tool_call_id,
                retry_node=recursive_retry_node,
            )

        invocation_id = f"delegation_{uuid.uuid4().hex[:12]}"
        effective_task_briefs_by_id = {
            str(task.get("taskBriefId") or "").strip(): dict(task)
            for task in normalized_tasks
            if isinstance(task, dict) and str(task.get("taskBriefId") or "").strip()
        }
        loaded_agents = _delegation_storage().get_all_agents()
        registry_snapshot, registry_agents = _registry_snapshot_from_state_or_agents(base_state, loaded_agents)
        registry_version = _registry_version(registry_snapshot)
        registry_hash = _registry_hash(registry_snapshot)
        external_descriptors = _delegation_external_worker_descriptors()
        dispatch_source = str(base_state.get("delegationDispatchSource") or inherited_context.get("delegationDispatchSource") or "").strip()
        compat_source = str(base_state.get("delegationCompatSource") or inherited_context.get("delegationCompatSource") or "").strip()
        if dispatch_task_source != "explicit" and not dispatch_source:
            dispatch_source = dispatch_task_source
        auto_dispatch_source = dispatch_source if dispatch_source.startswith("runtime_auto") else ""
        runtime_managed_dispatch = dispatch_source.startswith(("runtime_auto", "runtime_episode_runner"))
        # A runtime episode may use the Supervisor identity to dispatch its
        # implementation worker.  That is still a top-level delegation from
        # the authority perspective, but the worker episode belongs beneath
        # the owning capability episode for lifecycle, completion, and audit
        # purposes.  Keep this separate from ``parent_delegation_id``: the
        # latter is the recursive sub-agent depth boundary and must remain
        # empty for a Supervisor-owned dispatch.
        runtime_owner_episode_id = ""
        if supervisor_dispatch and runtime_managed_dispatch:
            route_context = inherited_context if isinstance(inherited_context, dict) else {}
            active_id = str(route_context.get("activeCapabilityEpisodeId") or "").strip()
            if active_id:
                matching_episode = next(
                    (
                        item
                        for item in list(route_context.get("capabilityEpisodes") or [])
                        if isinstance(item, dict)
                        and str(item.get("episodeId") or item.get("id") or "").strip() == active_id
                    ),
                    None,
                )
                matching_state = str((matching_episode or {}).get("state") or "").strip().lower()
                if matching_episode and matching_state not in TERMINAL_EPISODE_STATES:
                    runtime_owner_episode_id = active_id
        if supervisor_dispatch and not runtime_managed_dispatch:
            missing_named_targets = [
                str(task.get("taskBriefId") or f"task-{index + 1}").strip()
                for index, task in enumerate(normalized_tasks)
                if str(task.get("executionLaneHint") or "auto").strip().lower() != "external_worker"
                and not str(task.get("targetAgentName") or "").strip()
            ]
            if missing_named_targets:
                return Command(
                    goto="supervisor",
                    update={
                        "messages": [
                            ToolMessage(
                                content=_delegation_broker_payload(
                                    mode=normalized_mode,
                                    ok=False,
                                    summary="手工本地委派必须明确选择已注册子 Agent 的精确名称，不能只按家族或能力猜测。",
                                    error="target_agent_name_required",
                                    missingTaskBriefIds=missing_named_targets,
                                    availableAgents=_compact_registered_agent_catalog(registry_agents),
                                    recommended_next_action="Call agent_broker(mode='list'), then retry with task.targetAgentName.",
                                ),
                                tool_call_id=tool_call_id,
                            )
                        ]
                    },
                )
        workset_decisions = build_workset_dispatch_decisions(
            normalized_tasks,
            auto_dispatch=bool(auto_dispatch_source),
            decision_source="runtime_auto" if auto_dispatch_source else "supervisor_manual",
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
                        repair_suggestion=str(decision.get("repairSuggestion") or "Repair the Supervisor writeSet before dispatch.").strip(),
                        registry_version=registry_version,
                        registry_hash=registry_hash,
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
                                    "delegation_broker blocked dispatch because Engineering Runtime "
                                    "work-set governance found missing or conflicting write sets."
                                ),
                                items=blocked_items,
                                registryVersion=registry_version,
                                registryHash=registry_hash,
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
            if caller.is_direct_subagent and lane_hint == "external_worker":
                lane_hint = "subagent"
            local_agent = None
            local_diagnostics: dict[str, Any] = {}
            external_diagnostics: dict[str, Any] = {}
            if caller.is_direct_subagent and lane_hint in {"subagent", "auto"}:
                local_agent = next(
                    (
                        agent
                        for agent in registry_agents
                        if str(agent.get("id") or "").strip() == str(caller.agent_id or "").strip()
                        and agent.get("isEnabled") is not False
                    ),
                    None,
                )
                local_diagnostics = {
                    "selectionReason": "ephemeral_parent_mirror",
                    "selectionConfidence": 1.0 if local_agent else 0.0,
                    "matchSignals": [f"parentAgentId:{caller.agent_id}"],
                }
            elif lane_hint in {"subagent", "auto"}:
                local_agent, local_diagnostics = choose_best_local_agent_with_diagnostics(task_brief, registry_agents)
            external_worker = None
            if lane_hint == "external_worker":
                external_worker, external_diagnostics = choose_best_external_worker_with_diagnostics(task_brief, external_descriptors)
            elif lane_hint == "auto" and local_agent is None:
                external_worker, external_diagnostics = choose_best_external_worker_with_diagnostics(task_brief, external_descriptors)

            if local_agent and lane_hint != "external_worker":
                agent_id = str(local_agent.get("id") or "").strip()
                persistent_agent_name = str(local_agent.get("name") or agent_id).strip() or agent_id
                ephemeral_mirror = bool(caller.is_direct_subagent)
                ephemeral_agent_id = f"{agent_id}::worker-{index + 1:02d}" if ephemeral_mirror else ""
                agent_name = f"{persistent_agent_name} · worker-{index + 1:02d}" if ephemeral_mirror else persistent_agent_name
                target_id = ephemeral_agent_id or agent_id
                branch_task_brief = _with_recursive_delegation_access(task_brief)
                if current_depth > 0:
                    parent_task_brief = (
                        inherited_context.get("taskBrief")
                        if isinstance(inherited_context.get("taskBrief"), dict)
                        else {}
                    )
                    branch_task_brief = derive_grandchild_engineering_task(
                        parent_task_brief,
                        branch_task_brief,
                        shell_dialect=default_shell_dialect(),
                    )
                    branch_task_brief = _terminalize_grandchild_task_brief(branch_task_brief)
                active_collaborators = _active_collaborator_summaries(
                    normalized_tasks,
                    registry_agents,
                    current_index=index,
                    mirror_parent=local_agent if ephemeral_mirror else None,
                )
                branch_context_payload = (
                    dict(branch_task_brief.get("context") or {})
                    if isinstance(branch_task_brief.get("context"), dict)
                    else {"taskContext": str(branch_task_brief.get("context") or "").strip()}
                )
                if active_collaborators:
                    branch_context_payload["activeCollaborators"] = active_collaborators
                    branch_context_payload["collaborationBoundary"] = (
                        "These peers are concurrently active. Use their names and work summaries as reverse-boundary warnings: "
                        "do not duplicate or mutate their scope; return a conflict if your assigned boundary overlaps."
                    )
                if ephemeral_mirror:
                    branch_context_payload["ephemeralMirror"] = {
                        "agentId": ephemeral_agent_id,
                        "name": agent_name,
                        "parentAgentId": agent_id,
                        "parentAgentName": persistent_agent_name,
                        "disposable": True,
                        "persistToRegistry": False,
                    }
                    branch_task_brief["ephemeralMirror"] = True
                    branch_task_brief["ephemeralAgentId"] = ephemeral_agent_id
                    branch_task_brief["ephemeralParentAgentId"] = agent_id
                    branch_task_brief["targetAgentName"] = persistent_agent_name
                    branch_task_brief["ephemeralAgentName"] = agent_name
                    branch_task_brief["preferredAgentId"] = agent_id
                branch_task_brief["context"] = branch_context_payload
                branch_task_brief["delegationDepth"] = current_depth + 1
                effective_task_briefs_by_id[
                    str(branch_task_brief.get("taskBriefId") or "").strip()
                ] = dict(branch_task_brief)
                delegation_id_value = make_local_delegation_id(
                    invocation_id=invocation_id,
                    branch_index=index,
                    task_brief_id=str(branch_task_brief.get("taskBriefId") or ""),
                    agent_id=target_id,
                )
                managed_workspace: dict[str, Any] | None = None
                try:
                    managed_workspace = prepare_delegated_engineering_workspace(
                        base_state=base_state,
                        task_brief=branch_task_brief,
                        delegation_id=delegation_id_value,
                        current_depth=current_depth,
                        runtime_context=runtime_context,
                    )
                    if managed_workspace:
                        branch_task_brief = bind_engineering_task_workspace(
                            branch_task_brief,
                            workspace_path=str(managed_workspace.get("workspace_path") or ""),
                            original_workspace_path=str(
                                managed_workspace.get("original_workspace_path") or ""
                            ),
                        )
                        # The human instruction is built from the task brief.
                        # Recompute it after worktree binding; otherwise the
                        # worker receives a valid child lease but prose that
                        # still points at the parent's checkout.
                        task_query = task_brief_query_text(branch_task_brief) or str(
                            branch_task_brief.get("goal") or ""
                        ).strip()
                        task_goal = (
                            str(branch_task_brief.get("goal") or "").strip()
                            or task_query
                            or f"Task {index + 1}"
                        )
                        effective_task_briefs_by_id[
                            str(branch_task_brief.get("taskBriefId") or "").strip()
                        ] = dict(branch_task_brief)
                except Exception as exc:
                    error_code = str(getattr(exc, "code", None) or str(exc) or exc.__class__.__name__).strip()
                    parallel_results.append(
                        {
                            "invocationId": invocation_id,
                            "taskBriefId": str(branch_task_brief.get("taskBriefId") or f"{invocation_id}:{index}").strip(),
                            "taskBrief": branch_task_brief,
                            "taskGoal": task_goal,
                            "agentId": agent_id,
                            "agentName": agent_name,
                            "delegationId": delegation_id_value,
                            "lane": "subagent",
                            "targetId": target_id,
                            "targetLabel": agent_name,
                            "branchIndex": index,
                            "status": "error",
                            "error": error_code,
                            "localSelfCheck": "Managed worktree or native sandbox preparation failed before Agent execution.",
                            "acceptanceHint": "Repair the repository/sandbox readiness issue, then retry this delegation.",
                            "supervisorAcceptance": {
                                "status": "pending",
                                "requiredAction": ["retry", "ignore"],
                            },
                            "resultSchemaMatched": True,
                        }
                    )
                    items.append(
                        _delegation_compact_item(
                            delegation_id=delegation_id_value,
                            task_brief=task_brief,
                            lane="subagent",
                            target_id=target_id,
                            target_label=agent_name,
                            status="blocked",
                            invocation_id=invocation_id,
                            branch_index=index,
                            trace_ref=_delegation_trace_ref(
                                run_id=base_state.get("run_id"),
                                invocation_id=invocation_id,
                                branch_index=index,
                            ),
                            workset_dispatch_decision=workset_decision,
                            engineering_capsule_attached=True,
                            dispatch_blocked_reason=error_code,
                            repair_suggestion="Ensure Git is ready and the native sandbox host is available, then retry.",
                            registry_version=registry_version,
                            registry_hash=registry_hash,
                            error=error_code,
                        )
                    )
                    continue
                requested_plugin_references = list(branch_task_brief.get("pluginReferences") or [])
                if requested_plugin_references:
                    from runtimes.plugin_manager.service import plugin_manager_service

                    delegated_plugin_grants = plugin_manager_service.delegate_grants_to_subagent(
                        plugin_references=requested_plugin_references,
                        session_id=str(
                            base_state.get("session_id")
                            or runtime_context.get("session_id")
                            or runtime_context.get("sessionId")
                            or ""
                        ).strip(),
                        run_id=str(
                            base_state.get("run_id")
                            or runtime_context.get("run_id")
                            or runtime_context.get("runId")
                            or ""
                        ).strip(),
                        subagent_id=agent_id,
                        delegation_id=delegation_id_value,
                        delegation_depth=current_depth + 1,
                        parent_agent_id=caller.agent_id or None,
                        parent_delegation_id=caller.delegation_id or parent_delegation_id or None,
                    )
                    branch_task_brief["pluginGrantIds"] = [
                        str(item.get("grantId") or "")
                        for item in delegated_plugin_grants
                        if str(item.get("grantId") or "")
                    ]
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
                    selected_baseline_tools=inherited_context.get("selectedBaselineTools"),
                    prompt_addition=inherited_context.get("promptAddition"),
                    invocation_id=invocation_id,
                    task_brief=branch_task_brief,
                )
                branch_context.update(
                    {
                        "parentDelegationId": parent_delegation_id or None,
                        "delegationId": delegation_id_value,
                        "delegationDepth": current_depth + 1,
                        "delegationNodeCount": used_node_count + requested_count,
                        "delegationBudget": dict(recursive_policy),
                        "registryVersion": registry_version,
                        "registryHash": registry_hash,
                    }
                )
                if managed_workspace:
                    branch_context.update(managed_workspace)
                branch_state = dict(base_state)
                if managed_workspace:
                    branch_state.update(managed_workspace)
                instruction_owner = (
                    str(local_agent.get("name") or caller.agent_id or "Parent Agent").strip()
                    if caller.is_direct_subagent
                    else "Supervisor"
                )
                branch_state["messages"] = base_messages + [
                    HumanMessage(
                        content=f"[{instruction_owner} Delegated Task to {agent_name}]:\n{task_query or task_goal}",
                        additional_kwargs={
                            "v8_governance_type": "delegated_task_instruction",
                            "v8_task_brief_id": str(branch_task_brief.get("taskBriefId") or "").strip(),
                            "v8_delegation_id": delegation_id_value,
                        },
                    )
                ]
                branch_state["todos"] = list(base_todos)
                branch_state["delegation_contexts"] = base_contexts + [branch_context]
                branch_state["current_route_context"] = branch_context
                delegation_policy = _delegation_policy_from_task(branch_task_brief)
                policy_explicit = branch_task_brief.get("childDelegationPolicyExplicit")
                explicit_child_policy = (
                    next(
                        (
                            value
                            for value in (
                                branch_task_brief.get("allowChildDelegation"),
                                branch_task_brief.get("allow_child_delegation"),
                                delegation_policy.get("allowChildDelegation"),
                                delegation_policy.get("allow_child_delegation"),
                            )
                            if value is not None
                        ),
                        None,
                    )
                    if policy_explicit is True
                    else None
                )
                child_delegation_allowed = (
                    current_depth == 0
                    and (True if explicit_child_policy is None else bool(explicit_child_policy))
                )
                child_delegation_budget_for_branch = dict(
                    branch_task_brief.get("childDelegationBudget")
                    or branch_task_brief.get("child_delegation_budget")
                    or delegation_policy.get("childDelegationBudget")
                    or delegation_policy.get("child_delegation_budget")
                    or {}
                )
                if child_delegation_allowed and not child_delegation_budget_for_branch:
                    child_delegation_budget_for_branch = {"maxChildren": 1}
                branch_state["parallel_branch"] = {
                    "invocationId": invocation_id,
                    "branchIndex": index,
                    "agentId": agent_id,
                    "agentName": agent_name,
                    "targetId": target_id,
                    "ephemeralMirror": ephemeral_mirror,
                    "ephemeralAgentId": ephemeral_agent_id or None,
                    "ephemeralParentAgentId": agent_id if ephemeral_mirror else None,
                    "reason": task_goal,
                    "taskBriefId": str(branch_task_brief.get("taskBriefId") or f"{invocation_id}:{index}").strip(),
                    "taskBrief": branch_task_brief,
                    "delegationId": delegation_id_value,
                    "parentDelegationId": parent_delegation_id or None,
                    "delegationDepth": current_depth + 1,
                    "lane": "subagent",
                    "acceptanceHint": _delegation_acceptance_hint(branch_task_brief.get("acceptanceContract")),
                    "allowChildDelegation": child_delegation_allowed,
                    "childDelegationBudget": child_delegation_budget_for_branch,
                    "writeSetPartitions": list(delegation_policy.get("writeSetPartitions") or []),
                    "registryVersion": registry_version,
                    "registryHash": registry_hash,
                    "initialMessageCount": len(base_messages) + 1,
                    "initialTodoCount": len(base_todos),
                    **({"engineeringWorkspace": managed_workspace} if managed_workspace else {}),
                }
                sends.append(Send("parallel_delegate_task", branch_state))
                compact_item = _delegation_compact_item(
                        delegation_id=delegation_id_value,
                        task_brief=branch_task_brief,
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
                        registry_version=registry_version,
                        registry_hash=registry_hash,
                    )
                if managed_workspace:
                    compact_item["engineeringWorkspace"] = managed_workspace
                items.append(compact_item)
                continue

            if external_worker:
                external_workspace: dict[str, Any] | None = None
                external_seed = (
                    f"external::{invocation_id}::{index}::"
                    f"{str(task_brief.get('taskBriefId') or 'task')}::"
                    f"{str(external_worker.get('id') or 'worker')}"
                )
                try:
                    external_workspace = prepare_delegated_engineering_workspace(
                        base_state=base_state,
                        task_brief=task_brief,
                        delegation_id=external_seed,
                        current_depth=current_depth,
                        runtime_context=runtime_context,
                    )
                except Exception as exc:
                    error_code = str(getattr(exc, "code", None) or str(exc) or exc.__class__.__name__).strip()
                    item = _delegation_compact_item(
                        delegation_id=external_seed,
                        task_brief=task_brief,
                        lane="external_worker",
                        target_id=str(external_worker.get("id") or ""),
                        target_label=str(external_worker.get("name") or external_worker.get("id") or "external-worker"),
                        status="blocked",
                        invocation_id=invocation_id,
                        branch_index=index,
                        worker_type=str(external_worker.get("workerType") or "").strip() or None,
                        trace_ref=_delegation_trace_ref(
                            run_id=base_state.get("run_id"),
                            invocation_id=invocation_id,
                            branch_index=index,
                        ),
                        workset_dispatch_decision=workset_decision,
                        engineering_capsule_attached=bool(workset_decision.get("engineeringCapsuleAttached")),
                        registry_version=registry_version,
                        registry_hash=registry_hash,
                        error=error_code,
                    )
                    items.append(item)
                    parallel_results.append(item)
                    continue
                execution_workspace_path = str(
                    (external_workspace or {}).get("workspace_path")
                    or base_state.get("workspace_path")
                    or ""
                ).strip()
                rendered_command = render_external_worker_command(
                    descriptor=external_worker,
                    task_brief=task_brief,
                    workspace_path=execution_workspace_path,
                    workspace_id=str(base_state.get("workspace_id") or ""),
                    project_id=str(base_state.get("project_id") or ""),
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
                        registry_version=registry_version,
                        registry_hash=registry_hash,
                        error="missing_command_template",
                    )
                    items.append(item)
                    parallel_results.append(item)
                    continue

                external_runtime_context = {
                    **runtime_context,
                    **dict(external_workspace or {}),
                    "runtime_kind": "delegation",
                    "engineering_capsule_mode": "write",
                    "managed_engineering_execution": True,
                }
                with bind_runtime_context(**external_runtime_context):
                    raw_start_payload = _delegation_command_session_broker().func(
                        mode="start",
                        command=rendered_command,
                        cwd=execution_workspace_path,
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
                    workspace_path=execution_workspace_path,
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
                if external_workspace:
                    from core.engineering_sandbox.service import get_engineering_sandbox_service

                    get_engineering_sandbox_service().associate_worktree_delegation(
                        worktree_id=str(external_workspace.get("worktree_id") or ""),
                        delegation_id=delegation_id_value,
                    )
                managed_completion = _finalize_external_worker_workspace(
                    managed_workspace=external_workspace,
                    worker_status=worker_status,
                    run_id=str(base_state.get("run_id") or "").strip() or None,
                    invocation_id=invocation_id,
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
                    registry_version=registry_version,
                    registry_hash=registry_hash,
                    error=None if bool(start_payload.get("ok", True)) else str(start_payload.get("error") or "external_worker_start_failed"),
                )
                if external_workspace:
                    worker_item["engineeringWorkspace"] = external_workspace
                worker_item.update(managed_completion)
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
                registry_version=registry_version,
                registry_hash=registry_hash,
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
                        registryVersion=registry_version,
                        registryHash=registry_hash,
                        recommended_next_action=(
                            "observe"
                            if any(item.get("lane") == "external_worker" for item in items)
                            else "yield_for_graph_handoff"
                        ),
                        localHandoffPending=bool(sends),
                        localHandoffInstruction=(
                            "本地子 Agent 结果会由执行图自动回流。不要调用 wait 或 observe 轮询；结束当前执行片段并等待结构化回流。"
                            if sends
                            else None
                        ),
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
        dispatch_route_context = dict(inherited_context or {})
        session_id = str(
            base_state.get("session_id")
            or base_state.get("sessionId")
            or runtime_context.get("session_id")
            or runtime_context.get("sessionId")
            or ""
        ).strip() or None
        run_id = str(
            base_state.get("run_id")
            or base_state.get("runId")
            or runtime_context.get("run_id")
            or runtime_context.get("runId")
            or ""
        ).strip() or None
        workspace_path = str(
            base_state.get("workspace_path")
            or base_state.get("workspacePath")
            or inherited_context.get("workspace_path")
            or inherited_context.get("workspacePath")
            or runtime_context.get("workspace_path")
            or runtime_context.get("workspacePath")
            or ""
        ).strip()
        task_briefs_by_id = dict(effective_task_briefs_by_id)
        episode_root_id = _delegation_root_episode_id(
            inherited_context,
            parent_episode_id=parent_delegation_id,
            runtime_owner_episode_id=runtime_owner_episode_id,
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            delegation_id_value = str(item.get("delegationId") or "").strip()
            if not delegation_id_value:
                continue
            status = str(item.get("status") or "").strip().lower()
            episode_state = "failed" if status in {"error", "blocked", "failed"} else "waiting"
            task_brief_value = task_briefs_by_id.get(str(item.get("taskBriefId") or "").strip(), {})
            managed_workspace = (
                dict(item.get("engineeringWorkspace") or {})
                if isinstance(item.get("engineeringWorkspace"), dict)
                else {}
            )
            upstream_handoff_refs = [
                str(ref or "").strip()
                for ref in list(task_brief_value.get("upstreamHandoffRefs") or [])
                if str(ref or "").strip()
            ]
            episode = build_runtime_episode(
                need={
                    "kind": "delegation",
                    "needId": delegation_id_value,
                    "source": "delegation_broker",
                    "reason": str(item.get("taskGoal") or item.get("targetLabel") or "delegated task"),
                    "parentEpisodeId": parent_delegation_id or runtime_owner_episode_id,
                    "rootEpisodeId": episode_root_id,
                    "inputs": {
                        "targetCount": 1,
                        "workerBriefs": [task_brief_value],
                        **({"workspacePath": workspace_path} if workspace_path else {}),
                        **({"engineeringWorkspace": managed_workspace} if managed_workspace else {}),
                    },
                    "handoffRefs": upstream_handoff_refs,
                },
                kind="delegation",
                state=episode_state,
                required_runtime_access=list(task_brief_value.get("runtimeAccess") or []),
                parent_episode_id=parent_delegation_id or runtime_owner_episode_id,
                continuation_target="parallel_delegate_join" if item.get("lane") == "subagent" else "delegation_broker.observe",
                extra={
                    "invocationId": invocation_id,
                    "taskBriefId": item.get("taskBriefId"),
                    "targetId": item.get("targetId"),
                    "targetLabel": item.get("targetLabel"),
                    "lane": item.get("lane"),
                    "branchIndex": item.get("branchIndex"),
                    "registryVersion": item.get("registryVersion") or registry_version,
                    "registryHash": item.get("registryHash") or registry_hash,
                    "ownerEpisodeId": runtime_owner_episode_id or None,
                    "rootEpisodeId": episode_root_id or None,
                    **({"engineeringWorkspace": managed_workspace} if managed_workspace else {}),
                    "error": item.get("error"),
                },
            )
            persisted_episode = persist_runtime_episode(
                episode,
                session_id=session_id,
                run_id=run_id,
                priority=40,
                enqueue=False,
            )
            dispatch_route_context = upsert_runtime_episode(dispatch_route_context, persisted_episode)
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
                            summary=(
                                "尚未找到该本地子代理的结构化回流。"
                                if normalized_mode == "observe"
                                else "当前 resume/interrupt 仅支持 external_worker delegationId。"
                            ),
                            recommended_next_action="wait_for_graph_handoff" if normalized_mode == "observe" else "dispatch",
                            error="manual_local_delegation_polling_forbidden" if normalized_mode == "observe" else "unsupported_lane",
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
    from core.engineering_sandbox.service import get_engineering_sandbox_service

    managed_workspace = get_engineering_sandbox_service().managed_workspace_for_delegation(delegation_id)

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
        workspace_path=str(
            (managed_workspace or {}).get("workspace_path")
            or base_state.get("workspace_path")
            or ""
        ),
    )
    worker_status = str(payload.get("state") or ("terminated" if normalized_mode == "interrupt" else "running")).strip() or "running"
    if worker_result:
        worker_status = _external_worker_status_from_result(worker_result, fallback="succeeded")
    elif worker_status in {"completed", "failed"}:
        worker_status = "marker_missing"
    managed_completion = _finalize_external_worker_workspace(
        managed_workspace=managed_workspace,
        worker_status=worker_status,
        run_id=str(base_state.get("run_id") or base_state.get("runId") or "").strip() or None,
        invocation_id=str((managed_workspace or {}).get("worktree_id") or command_id or "external-worker"),
    )
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
    if managed_workspace:
        worker_item["engineeringWorkspace"] = managed_workspace
    worker_item.update(managed_completion)
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


__all__ = [name for name in globals() if name.startswith("_delegation") or name in {"delegation_broker", "request_peer_help", "_with_recursive_delegation_access", "_normalize_external_worker_result_paths", "_external_worker_status_from_result", "_coerce_delegation_json_value", "_coerce_delegation_list", "_coerce_delegation_dict", "_delegation_task_has_meaningful_content", "_filter_meaningful_delegation_tasks", "_safe_int_range"}]

