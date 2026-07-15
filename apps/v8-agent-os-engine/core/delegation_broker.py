from __future__ import annotations

import base64
import json
import os
import re
import shlex
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from core.command_environment import default_shell_dialect
from core.engineering_capsule import ensure_engineering_task_capsule

from core.agents import normalize_specialist_family_id

from core.runtime_tool_access import normalize_subagent_runtime_bindings


_WORD_PATTERN = re.compile(r"[a-z0-9_+.-]+", re.IGNORECASE)


def _tokenize(value: Any) -> list[str]:
    text = str(value or "").strip().lower()
    if not text:
        return []
    return [token for token in _WORD_PATTERN.findall(text) if token]


def _unique_str_list(values: Iterable[Any] | None) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def _as_string_list(values: Iterable[Any] | None) -> list[str]:
    return [str(item).strip() for item in list(values or []) if str(item).strip()]


def _normalize_scope_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return _unique_str_list(value)
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else []
    return [str(value).strip()] if str(value).strip() else []


def _normalize_plugin_references(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    merged: dict[str, set[str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        plugin_id = str(item.get("pluginId") or item.get("plugin_id") or "").strip().lower()
        component_ids = {
            str(component).strip()
            for component in list(item.get("componentIds") or item.get("component_ids") or [])
            if str(component).strip()
        }
        if not plugin_id or not component_ids:
            continue
        merged.setdefault(plugin_id, set()).update(component_ids)
    return [
        {"pluginId": plugin_id, "componentIds": sorted(component_ids)}
        for plugin_id, component_ids in sorted(merged.items())
    ]


def _first_present(payload: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _safe_target_count(value: Any, *, default: int = 1, maximum: int = 1000) -> int:
    try:
        count = int(value)
    except Exception:
        count = default
    return max(1, min(count, maximum))


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "allow", "allowed"}
    return bool(value)


def _normalize_worker_briefs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            copied = dict(item)
            if copied:
                items.append(copied)
            continue
        text = str(item or "").strip()
        if text:
            items.append({"goal": text})
    return items


def _normalize_acceptance_tiers(value: Any) -> dict[str, list[str]]:
    parsed = value
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
        else:
            parsed = ""
    tiers: dict[str, list[str]] = {"must": [], "should": [], "nice": []}
    if isinstance(parsed, dict):
        aliases = {
            "must": ("must", "required", "hard", "mustHave", "must_have"),
            "should": ("should", "recommended", "soft", "shouldHave", "should_have"),
            "nice": ("nice", "niceToHave", "nice_to_have", "optional"),
        }
        for tier, keys in aliases.items():
            values: list[Any] = []
            for key in keys:
                raw = parsed.get(key)
                if isinstance(raw, (list, tuple, set)):
                    values.extend(list(raw))
                elif raw:
                    values.append(raw)
            tiers[tier] = _unique_str_list(values)
        if not any(tiers.values()):
            tiers["must"] = _unique_str_list(parsed.values())
        return tiers
    if isinstance(parsed, (list, tuple, set)):
        tiers["must"] = _unique_str_list(parsed)
        return tiers
    text = str(parsed or "").strip()
    if text:
        tiers["must"] = [text]
    return tiers


def _default_task_brief(index: int = 0) -> dict[str, Any]:
    return {
        "taskBriefId": f"task-{index + 1}",
        "goal": "",
        "context": "",
        "routeQuery": "",
        "writeSet": [],
        "expectedOutputs": [],
        "behaviorScope": [],
        "requiredCapabilities": [],
        "runtimeAccess": [],
        "toolPolicy": {"mode": "default", "allowedTools": [], "forbiddenTools": []},
        "allowedTools": [],
        "forbiddenTools": [],
        "pluginReferences": [],
        "evidenceRefs": [],
        "detailRefs": [],
        "acceptanceContract": "",
        "acceptanceTiers": {"must": [], "should": [], "nice": []},
        "dependency": [],
        "parallelGroup": "",
        "executionLaneHint": "auto",
        "familyHint": "",
        "preferredAgentId": "",
        "preferredWorkerType": "",
        "researchRefs": [],
        "targetCount": 1,
        "workerBriefs": [],
        "fanoutReason": "",
        "allowChildDelegation": False,
        "childDelegationBudget": {},
        "writeSetPartitions": [],
    }


def normalize_task_brief(value: Any, *, index: int = 0) -> dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    defaults = _default_task_brief(index)
    worker_briefs = _normalize_worker_briefs(
        _first_present(payload, ("workerBriefs", "worker_briefs", "workers", "branches", "parallelBranches", "parallel_branches"))
    )
    target_count = _safe_target_count(
        _first_present(
            payload,
            (
                "targetCount",
                "target_count",
                "parallelism",
                "parallelismCount",
                "parallelism_count",
                "workerCount",
                "worker_count",
                "agentCount",
                "agent_count",
                "fanout",
                "fanoutCount",
                "fanout_count",
            ),
        )
    )
    if worker_briefs:
        target_count = max(target_count, len(worker_briefs))
    child_delegation_budget = _first_present(payload, ("childDelegationBudget", "child_delegation_budget", "childBudget", "child_budget"))
    write_set_partitions = _first_present(payload, ("writeSetPartitions", "write_set_partitions", "writePartitions", "write_partitions"))
    acceptance_contract = _first_present(payload, ("acceptanceContract", "acceptance_contract", "acceptance"))
    acceptance_tiers = _first_present(payload, ("acceptanceTiers", "acceptance_tiers", "tieredAcceptance", "tiered_acceptance"))
    normalized_acceptance_tiers = _normalize_acceptance_tiers(acceptance_tiers if acceptance_tiers is not None else acceptance_contract)
    raw_tool_policy = _first_present(payload, ("toolPolicy", "tool_policy"))
    tool_policy = dict(raw_tool_policy or {}) if isinstance(raw_tool_policy, dict) else {}
    allowed_tools_present = any(key in payload for key in ("allowedTools", "allowed_tools")) or any(
        key in tool_policy for key in ("allowedTools", "allowed_tools")
    )
    allowed_tools = _normalize_scope_values(
        _first_present(payload, ("allowedTools", "allowed_tools"))
        if any(key in payload for key in ("allowedTools", "allowed_tools"))
        else _first_present(tool_policy, ("allowedTools", "allowed_tools"))
    )
    forbidden_tools = _normalize_scope_values(
        _first_present(payload, ("forbiddenTools", "forbidden_tools"))
        or _first_present(tool_policy, ("forbiddenTools", "forbidden_tools"))
    )
    no_tools = _safe_bool(
        _first_present(payload, ("noTools", "no_tools"))
        or _first_present(tool_policy, ("noTools", "no_tools"))
    )
    tool_policy_mode = str(tool_policy.get("mode") or "").strip().lower()
    if no_tools:
        tool_policy_mode = "none"
        allowed_tools = []
    elif tool_policy_mode not in {"default", "allowlist", "none"}:
        tool_policy_mode = "allowlist" if allowed_tools_present else "default"
    behavior_scope = _normalize_scope_values(payload.get("behaviorScope") or payload.get("behavior_scope"))
    for boundary in _normalize_scope_values(
        _first_present(payload, ("constraints", "constraint", "boundaries", "boundary"))
    ):
        if boundary not in behavior_scope:
            behavior_scope.append(boundary)
    expected_artifacts = _normalize_scope_values(
        _first_present(payload, ("expectedArtifacts", "expected_artifacts"))
    )
    expected_outputs = _normalize_scope_values(
        _first_present(
            payload,
            (
                "expectedOutputs",
                "expected_outputs",
                "expectedOutput",
                "expected_output",
            ),
        )
    )
    if not expected_outputs and expected_artifacts:
        # Historical callers used expectedArtifacts as the only output field.
        # Preserve that minimum compatibility without erasing the distinction:
        # expectedOutputs describes acceptance-facing results, while
        # expectedArtifacts contains concrete artifact paths only.
        expected_outputs = list(expected_artifacts)
    normalized = {
        "taskBriefId": str(payload.get("taskBriefId") or payload.get("task_brief_id") or defaults["taskBriefId"]).strip(),
        "goal": str(payload.get("goal") or "").strip(),
        "context": payload.get("context") if isinstance(payload.get("context"), dict) else str(payload.get("context") or "").strip(),
        "routeQuery": str(payload.get("routeQuery") or payload.get("route_query") or payload.get("extensionsRouteQuery") or payload.get("extensions_route_query") or "").strip(),
        "writeSet": _normalize_scope_values(payload.get("writeSet") or payload.get("write_set")),
        "expectedOutputs": expected_outputs,
        "expectedArtifacts": expected_artifacts,
        "behaviorScope": behavior_scope,
        "requiredCapabilities": _normalize_scope_values(payload.get("requiredCapabilities") or payload.get("required_capabilities")),
        "runtimeAccess": _normalize_scope_values(payload.get("runtimeAccess") or payload.get("runtime_access")),
        "toolPolicy": {
            "mode": tool_policy_mode,
            "allowedTools": allowed_tools,
            "forbiddenTools": forbidden_tools,
        },
        "allowedTools": allowed_tools,
        "forbiddenTools": forbidden_tools,
        "pluginReferences": _normalize_plugin_references(
            payload.get("pluginReferences") or payload.get("plugin_references")
        ),
        "evidenceRefs": _normalize_scope_values(payload.get("evidenceRefs") or payload.get("evidence_refs")),
        "detailRefs": _normalize_scope_values(payload.get("detailRefs") or payload.get("detail_refs")),
        "acceptanceContract": acceptance_contract if isinstance(acceptance_contract, dict) else str(acceptance_contract or "").strip(),
        "acceptanceTiers": normalized_acceptance_tiers,
        "dependency": _normalize_scope_values(payload.get("dependency")),
        "parallelGroup": str(payload.get("parallelGroup") or payload.get("parallel_group") or "").strip(),
        "executionLaneHint": str(payload.get("executionLaneHint") or payload.get("execution_lane_hint") or "auto").strip().lower() or "auto",
        "familyHint": str(payload.get("familyHint") or payload.get("family_hint") or payload.get("specialistFamily") or "").strip(),
        "preferredAgentId": str(payload.get("preferredAgentId") or payload.get("preferred_agent_id") or "").strip(),
        "preferredWorkerType": str(payload.get("preferredWorkerType") or payload.get("preferred_worker_type") or "").strip(),
        "researchRefs": _normalize_scope_values(payload.get("researchRefs") or payload.get("research_refs")),
        "targetCount": target_count,
        "workerBriefs": worker_briefs,
        "fanoutReason": str(payload.get("fanoutReason") or payload.get("fanout_reason") or payload.get("parallelismReason") or payload.get("parallelism_reason") or "").strip(),
        "allowChildDelegation": _safe_bool(
            _first_present(payload, ("allowChildDelegation", "allow_child_delegation", "allowNestedDelegation", "allow_nested_delegation"))
        ),
        "childDelegationBudget": (
            dict(child_delegation_budget or {})
            if isinstance(child_delegation_budget, dict)
            else {}
        ),
        "writeSetPartitions": [
            dict(item) if isinstance(item, dict) else item
            for item in list(write_set_partitions or [])
        ]
        if isinstance(write_set_partitions, list)
        else [],
    }
    for key in ("criticalFiles", "readSet", "verificationMatrix", "proofExpectations"):
        normalized[key] = _normalize_scope_values(payload.get(key) or payload.get(key[0].lower() + key[1:]))
    if isinstance(payload.get("engineeringTaskCapsule"), dict):
        normalized["engineeringTaskCapsule"] = dict(payload.get("engineeringTaskCapsule") or {})
    elif isinstance(payload.get("engineering_task_capsule"), dict):
        normalized["engineeringTaskCapsule"] = dict(payload.get("engineering_task_capsule") or {})
    if "deliverableKind" in payload or "deliverable_kind" in payload:
        normalized["deliverableKind"] = str(
            _first_present(payload, ("deliverableKind", "deliverable_kind")) or ""
        ).strip()
    if "writeRequired" in payload or "write_required" in payload:
        normalized["writeRequired"] = _safe_bool(
            _first_present(payload, ("writeRequired", "write_required"))
        )
    if "readOnly" in payload or "read_only" in payload:
        normalized["readOnly"] = _safe_bool(
            _first_present(payload, ("readOnly", "read_only"))
        )
    if "validateSkillArtifact" in payload or "validate_skill_artifact" in payload:
        normalized["validateSkillArtifact"] = _safe_bool(
            _first_present(payload, ("validateSkillArtifact", "validate_skill_artifact"))
        )
    if "requiredSkillContracts" in payload or "required_skill_contracts" in payload:
        normalized["requiredSkillContracts"] = _normalize_scope_values(
            payload.get("requiredSkillContracts") or payload.get("required_skill_contracts")
        )
    if normalized["executionLaneHint"] not in {"subagent", "external_worker", "engineering", "auto"}:
        normalized["executionLaneHint"] = "auto"
    if not normalized["taskBriefId"]:
        normalized["taskBriefId"] = defaults["taskBriefId"]
    return ensure_engineering_task_capsule(
        normalized,
        shell_dialect=default_shell_dialect(),
    )


def normalize_task_briefs(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [normalize_task_brief(value, index=index) for index, value in enumerate(list(values or []))]


def _merge_worker_context(parent_context: Any, worker_context: Any, *, parent_goal: str, index: int, count: int) -> Any:
    if not worker_context:
        return {
            "parentContext": parent_context,
            "parentGoal": parent_goal,
            "parallelWorker": {"index": index + 1, "count": count},
        }
    if isinstance(parent_context, dict) or isinstance(worker_context, dict):
        return {
            "parentContext": parent_context,
            "workerContext": worker_context,
            "parentGoal": parent_goal,
            "parallelWorker": {"index": index + 1, "count": count},
        }
    return (
        f"{str(parent_context or '').strip()}\n\n"
        f"[Parallel worker {index + 1}/{count}]\n"
        f"Parent goal: {parent_goal}\n"
        f"Worker context: {str(worker_context or '').strip()}"
    ).strip()


def expand_delegation_task_briefs(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Expand macro task briefs that explicitly request multiple parallel workers.

    The supervisor owns the requested fanout. A single macro task may
    request targetCount=3 or provide three workerBriefs; budget enforcement still
    happens later on the expanded branch count.
    """

    expanded: list[dict[str, Any]] = []
    macro_tasks = normalize_task_briefs(values or [])
    for macro_index, macro in enumerate(macro_tasks):
        worker_briefs = _normalize_worker_briefs(macro.get("workerBriefs"))
        count = _safe_target_count(macro.get("targetCount"), default=1)
        if worker_briefs:
            count = max(count, len(worker_briefs))
        if count <= 1:
            item = dict(macro)
            item["targetCount"] = 1
            item["workerBriefs"] = []
            expanded.append(item)
            continue

        parent_id = str(macro.get("taskBriefId") or f"task-{macro_index + 1}").strip() or f"task-{macro_index + 1}"
        parent_goal = str(macro.get("goal") or "").strip()
        for worker_index in range(count):
            worker = dict(worker_briefs[worker_index]) if worker_index < len(worker_briefs) else {}
            branch_payload = deepcopy(macro)
            branch_payload["taskBriefId"] = str(
                worker.get("taskBriefId")
                or worker.get("task_brief_id")
                or worker.get("id")
                or f"{parent_id}#worker-{worker_index + 1}"
            ).strip()
            if worker.get("goal"):
                branch_payload["goal"] = str(worker.get("goal") or "").strip()
            else:
                branch_payload["goal"] = f"{parent_goal} (parallel branch {worker_index + 1}/{count})".strip()
            branch_payload["context"] = _merge_worker_context(
                macro.get("context"),
                worker.get("context"),
                parent_goal=parent_goal,
                index=worker_index,
                count=count,
            )
            for key in (
                "routeQuery",
                "route_query",
                "writeSet",
                "write_set",
                "readSet",
                "read_set",
                "criticalFiles",
                "critical_files",
                "behaviorScope",
                "behavior_scope",
                "requiredCapabilities",
                "required_capabilities",
                "runtimeAccess",
                "runtime_access",
                "toolPolicy",
                "tool_policy",
                "allowedTools",
                "allowed_tools",
                "forbiddenTools",
                "forbidden_tools",
                "noTools",
                "no_tools",
                "pluginReferences",
                "plugin_references",
                "acceptanceContract",
                "acceptance_contract",
                "dependency",
                "parallelGroup",
                "parallel_group",
                "executionLaneHint",
                "execution_lane_hint",
                "familyHint",
                "family_hint",
                "preferredAgentId",
                "preferred_agent_id",
                "preferredWorkerType",
                "preferred_worker_type",
                "researchRefs",
                "research_refs",
                "verificationMatrix",
                "verification_matrix",
                "proofExpectations",
                "proof_expectations",
                "engineeringTaskCapsule",
                "engineering_task_capsule",
            ):
                if key in worker:
                    branch_payload[key] = worker.get(key)
            branch_payload["targetCount"] = 1
            branch_payload["workerBriefs"] = []
            branch = normalize_task_brief(branch_payload, index=len(expanded))
            branch["parentTaskBriefId"] = parent_id
            branch["siblingIndex"] = worker_index + 1
            branch["siblingCount"] = count
            branch["fanoutReason"] = str(worker.get("fanoutReason") or macro.get("fanoutReason") or "").strip()
            branch["workerBrief"] = worker
            expanded.append(branch)
    return expanded


def build_minimal_task_brief(
    *,
    goal: str,
    task_brief_id: str | None = None,
    preferred_agent_id: str | None = None,
    execution_lane_hint: str = "subagent",
) -> dict[str, Any]:
    brief = normalize_task_brief(
        {
            "taskBriefId": task_brief_id or "",
            "goal": goal,
            "context": "",
            "routeQuery": "",
            "writeSet": [],
            "behaviorScope": [],
            "requiredCapabilities": [],
            "acceptanceContract": "",
            "dependency": [],
            "parallelGroup": "",
            "executionLaneHint": execution_lane_hint,
            "familyHint": "",
            "preferredAgentId": preferred_agent_id or "",
        }
    )
    return brief


def _stringify_context(value: Any) -> str:
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    return str(value or "").strip()


def task_brief_query_text(task_brief: dict[str, Any] | None) -> str:
    if not isinstance(task_brief, dict):
        return ""
    parts: list[str] = []
    goal = str(task_brief.get("goal") or "").strip()
    if goal:
        parts.append(goal)
    context_text = _stringify_context(task_brief.get("context"))
    if context_text:
        parts.append(f"Context: {context_text}")
    capabilities = [str(item).strip() for item in list(task_brief.get("requiredCapabilities") or []) if str(item).strip()]
    if capabilities:
        parts.append(f"Required capabilities: {', '.join(capabilities)}")
    write_set = [str(item).strip() for item in list(task_brief.get("writeSet") or []) if str(item).strip()]
    if write_set:
        parts.append(f"Write set: {', '.join(write_set)}")
    behavior_scope = [str(item).strip() for item in list(task_brief.get("behaviorScope") or []) if str(item).strip()]
    if behavior_scope:
        parts.append(f"Behavior scope: {', '.join(behavior_scope)}")
    raw_family_hint = str(task_brief.get("familyHint") or "").strip()
    family_hint = normalize_specialist_family_id(raw_family_hint) if raw_family_hint else ""
    if family_hint:
        parts.append(f"Family hint: {family_hint}")
    research_refs = [str(item).strip() for item in list(task_brief.get("researchRefs") or []) if str(item).strip()]
    if research_refs:
        parts.append(f"Research refs: {', '.join(research_refs)}")
    acceptance = _stringify_context(task_brief.get("acceptanceContract"))
    if acceptance:
        parts.append(f"Acceptance contract: {acceptance}")
    return "\n".join(part for part in parts if part).strip()


_ROUTE_QUERY_NOISE_VALUES = {
    "acceptance",
    "acceptance_contract",
    "artifact_ref",
    "contract",
    "documentation",
    "final_acceptance",
    "handoff",
    "proposal",
    "review",
    "verification",
    "verification_contract",
    "verify",
}


_ENGINEERING_FILE_EXTENSIONS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".vue",
    ".svelte",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
)

_ENGINEERING_TASK_TERMS = {
    "code",
    "coding",
    "implementation",
    "implement",
    "debug",
    "fix",
    "test",
    "typecheck",
    "build",
    "lint",
    "refactor",
    "review",
    "verify",
    "architecture",
    "docs",
    "documentation",
    "代码",
    "实现",
    "修复",
    "测试",
    "验证",
    "构建",
    "重构",
}

_READ_ONLY_ENGINEERING_TERMS = {
    "review",
    "audit",
    "verify",
    "verification",
    "validate",
    "analysis",
    "inspect",
    "read_only",
    "read-only",
    "diagnose",
    "reviewer",
    "verifier",
    "测试验证",
    "代码审查",
    "审查",
    "审计",
    "验证",
    "分析",
}

_DOC_ONLY_ENGINEERING_TERMS = {
    "docs",
    "documentation",
    "document",
    "delivery",
    "writer",
    "release_notes",
    "readme",
    "文档",
    "交付",
    "说明",
}

_WRITE_ENGINEERING_TERMS = {
    "implement",
    "implementation",
    "fix",
    "edit",
    "modify",
    "patch",
    "refactor",
    "migration",
    "write",
    "代码",
    "实现",
    "修改",
    "修复",
    "重构",
    "迁移",
}

_ROLE_SIGNAL_GROUPS = {
    "implementation": {
        "implement",
        "implementation",
        "fix",
        "debug",
        "refactor",
        "patch",
        "code",
        "coding",
        "engineer",
        "实现",
        "修复",
        "改代码",
    },
    "verification": {
        "verify",
        "verification",
        "test",
        "typecheck",
        "build",
        "lint",
        "validate",
        "verifier",
        "测试",
        "验证",
        "构建",
    },
    "review": {
        "review",
        "audit",
        "architect",
        "architecture",
        "risk",
        "compare",
        "代码审查",
        "审查",
        "架构",
        "风险",
    },
    "documentation": {
        "docs",
        "documentation",
        "writer",
        "delivery",
        "readme",
        "release",
        "文档",
        "交付",
        "说明",
    },
}


def _route_query_values(values: Iterable[Any] | None, *, max_items: int = 6) -> list[str]:
    selected: list[str] = []
    for value in _as_string_list(values):
        normalized = value.strip()
        if not normalized:
            continue
        normalized_key = normalized.lower().replace("-", "_").replace(" ", "_")
        if normalized_key in _ROUTE_QUERY_NOISE_VALUES:
            continue
        if normalized not in selected:
            selected.append(normalized)
        if len(selected) >= max_items:
            break
    return selected


def task_brief_route_query_text(task_brief: dict[str, Any] | None) -> str:
    """Return a compact delegated-task query for extension prefiltering.

    This deliberately keeps the full task brief out of Stage1/Stage2 routing:
    write sets, acceptance contracts, and long context often contain governance
    nouns such as "documentation" or "verification" that are not task intent.
    """
    if not isinstance(task_brief, dict):
        return ""
    explicit_query = str(
        task_brief.get("routeQuery")
        or task_brief.get("route_query")
        or task_brief.get("extensionsRouteQuery")
        or task_brief.get("extensions_route_query")
        or ""
    ).strip()
    if explicit_query:
        return explicit_query

    parts: list[str] = []
    goal = str(task_brief.get("goal") or "").strip()
    if goal:
        parts.append(goal)

    capabilities = _route_query_values(task_brief.get("requiredCapabilities"), max_items=5)
    if capabilities:
        parts.append(f"Required capabilities: {', '.join(capabilities)}")

    behavior_scope = _route_query_values(task_brief.get("behaviorScope"), max_items=4)
    if behavior_scope:
        parts.append(f"Behavior scope: {', '.join(behavior_scope)}")

    return "\n".join(part for part in parts if part).strip()


def task_brief_summary(task_brief: dict[str, Any] | None) -> str:
    if not isinstance(task_brief, dict):
        return ""
    goal = str(task_brief.get("goal") or "").strip()
    task_id = str(task_brief.get("taskBriefId") or "").strip()
    if goal and task_id:
        return f"{task_id}: {goal}"
    return goal or task_id


def _task_brief_signal_text(task_brief: dict[str, Any] | None) -> str:
    if not isinstance(task_brief, dict):
        return ""
    capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
    parts: list[str] = [
        str(task_brief.get("goal") or ""),
        _stringify_context(task_brief.get("context")),
        " ".join(_as_string_list(task_brief.get("behaviorScope"))),
        " ".join(_as_string_list(task_brief.get("requiredCapabilities"))),
        " ".join(_as_string_list(task_brief.get("writeSet"))),
        " ".join(_as_string_list(task_brief.get("criticalFiles"))),
        " ".join(_as_string_list(task_brief.get("readSet"))),
        " ".join(_as_string_list(capsule.get("riskFlags"))),
        " ".join(_as_string_list(capsule.get("proofExpectations"))),
    ]
    return " ".join(part for part in parts if part).strip().lower()


def infer_engineering_task_role(task_brief: dict[str, Any] | None) -> str:
    """Infer the engineering role this task most likely needs.

    This is intentionally lightweight; it biases selection, but does not replace
    capabilitySnapshot matching or preferredAgentId.
    """
    text = _task_brief_signal_text(task_brief)
    if not text:
        return ""
    scores: dict[str, int] = {role: 0 for role in _ROLE_SIGNAL_GROUPS}
    for role, terms in _ROLE_SIGNAL_GROUPS.items():
        for term in terms:
            if term and term in text:
                scores[role] += 1
    role, score = max(scores.items(), key=lambda item: item[1])
    return role if score > 0 else ""


def _task_is_engineering_like(task_brief: dict[str, Any] | None) -> bool:
    if not isinstance(task_brief, dict):
        return False
    if isinstance(task_brief.get("engineeringTaskCapsule"), dict) and task_brief.get("engineeringTaskCapsule"):
        return True
    for key in ("criticalFiles", "readSet", "verificationMatrix", "proofExpectations"):
        if list(task_brief.get(key) or []):
            return True
    paths = _as_string_list(task_brief.get("writeSet")) + _as_string_list(task_brief.get("criticalFiles")) + _as_string_list(task_brief.get("readSet"))
    if any(str(path).lower().endswith(_ENGINEERING_FILE_EXTENSIONS) or "/" in str(path).replace("\\", "/") for path in paths):
        return True
    tokens = set(_tokenize(_task_brief_signal_text(task_brief)))
    return bool(tokens & _ENGINEERING_TASK_TERMS)


def _task_is_read_only_safe(task_brief: dict[str, Any] | None) -> bool:
    if not isinstance(task_brief, dict):
        return False
    scope_tokens = set(
        _tokenize(
            " ".join(
                [
                    " ".join(_as_string_list(task_brief.get("behaviorScope"))),
                    " ".join(_as_string_list(task_brief.get("requiredCapabilities"))),
                ]
            )
        )
    )
    if scope_tokens & _READ_ONLY_ENGINEERING_TERMS and not (scope_tokens & _WRITE_ENGINEERING_TERMS):
        return True
    tokens = set(_tokenize(_task_brief_signal_text(task_brief)))
    if tokens & _WRITE_ENGINEERING_TERMS:
        return False
    capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
    if str(capsule.get("executionMode") or "").strip().lower() in {"read_only", "verify"}:
        return True
    if tokens & _READ_ONLY_ENGINEERING_TERMS:
        return True
    if tokens & _DOC_ONLY_ENGINEERING_TERMS and not _as_string_list(task_brief.get("writeSet")):
        return True
    return False


def _normalize_workset_path(value: Any) -> str:
    normalized = str(value or "").strip().replace("\\", "/").lower()
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized.rstrip("/") if normalized not in {"", "/"} else normalized


def _path_overlaps(left: str, right: str) -> bool:
    a = _normalize_workset_path(left)
    b = _normalize_workset_path(right)
    if not a or not b:
        return False
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _normalized_write_set(task_brief: dict[str, Any] | None) -> list[str]:
    if not isinstance(task_brief, dict):
        return []
    capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
    values = _as_string_list(task_brief.get("writeSet")) or _as_string_list(capsule.get("writeSet"))
    return _unique_str_list(values)


def engineering_task_capsule(task_brief: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(task_brief, dict):
        return {}
    raw_capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
    capsule = {
        "executionMode": raw_capsule.get("executionMode"),
        "contractStatus": raw_capsule.get("contractStatus"),
        "missingContractFields": _unique_str_list(raw_capsule.get("missingContractFields")),
        "criticalFiles": _unique_str_list(raw_capsule.get("criticalFiles") or task_brief.get("criticalFiles")),
        "readSet": _unique_str_list(raw_capsule.get("readSet") or task_brief.get("readSet")),
        "writeSet": _unique_str_list(raw_capsule.get("writeSet") or task_brief.get("writeSet")),
        "verificationContract": list(raw_capsule.get("verificationContract") or []),
        "proofExpectations": _unique_str_list(raw_capsule.get("proofExpectations") or task_brief.get("proofExpectations")),
        "riskFlags": _unique_str_list(raw_capsule.get("riskFlags")),
    }
    return {key: value for key, value in capsule.items() if value not in (None, "", [], {})}


def build_workset_dispatch_decisions(
    task_briefs: Iterable[dict[str, Any]] | None,
    *,
    auto_dispatch: bool = False,
    decision_source: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate Engineering Runtime write-set safety before broker dispatch."""
    normalized_tasks = [normalize_task_brief(item, index=index) for index, item in enumerate(list(task_briefs or []))]
    decisions: list[dict[str, Any]] = []
    write_owners: list[tuple[int, str, str]] = []
    source = str(decision_source or ("supervisor_auto" if auto_dispatch else "supervisor_manual")).strip() or (
        "supervisor_auto" if auto_dispatch else "supervisor_manual"
    )

    for index, task in enumerate(normalized_tasks):
        task_id = str(task.get("taskBriefId") or f"task-{index + 1}").strip()
        write_set = _normalized_write_set(task)
        capsule = engineering_task_capsule(task)
        engineering_like = _task_is_engineering_like(task)
        read_only_safe = _task_is_read_only_safe(task)
        blocked = False
        warning = False
        risk: str | None = None
        reason = "task_has_no_engineering_workset_requirements"
        repair = ""

        if engineering_like and str(capsule.get("contractStatus") or "valid") == "invalid":
            missing_fields = _unique_str_list(capsule.get("missingContractFields"))
            risk = "invalid_execution_contract"
            reason = "engineering_task_capsule_incomplete"
            repair = (
                "Repair the task brief before dispatch. Missing: "
                + ", ".join(missing_fields or ["writeSet", "expectedOutputs", "acceptanceContract"])
                + "."
            )
            blocked = True
            warning = True
        elif engineering_like:
            if write_set:
                risk = "within_write_set"
                reason = "declared_write_set_present"
            elif read_only_safe:
                risk = "read_only_safe"
                reason = "read_only_or_doc_only_task_without_write_set"
            else:
                risk = "missing_write_set"
                reason = "engineering_task_missing_write_set"
                repair = "Repair the Supervisor task brief with a concrete writeSet, or mark the task read-only/review-only before auto-dispatch."
                blocked = bool(auto_dispatch)
                warning = True

        decision = {
            "taskBriefId": task_id,
            "mode": "auto" if auto_dispatch else "manual",
            "worksetDecisionSource": source,
            "blocked": blocked,
            "warning": warning or blocked,
            "reason": reason,
            "writeSet": write_set,
            "engineeringCapsuleAttached": bool(capsule),
            "engineeringRole": infer_engineering_task_role(task),
            "repairSuggestion": repair,
        }
        if risk:
            decision["risk"] = risk
            decision["correlationStatus"] = risk
        decisions.append(decision)
        if engineering_like and write_set and not read_only_safe:
            for path in write_set:
                write_owners.append((index, task_id, path))

    conflicts_by_index: dict[int, list[dict[str, Any]]] = {}
    for left_pos, (left_index, left_task, left_path) in enumerate(write_owners):
        for right_index, right_task, right_path in write_owners[left_pos + 1 :]:
            if left_task == right_task:
                continue
            if _path_overlaps(left_path, right_path):
                conflict = {
                    "tasks": [left_task, right_task],
                    "paths": [left_path, right_path],
                }
                conflicts_by_index.setdefault(left_index, []).append(conflict)
                conflicts_by_index.setdefault(right_index, []).append(conflict)

    for index, conflicts in conflicts_by_index.items():
        decision = decisions[index]
        decision["risk"] = "outside_write_set"
        decision["warning"] = True
        decision["blocked"] = bool(auto_dispatch)
        decision["correlationStatus"] = "outside_write_set"
        decision["worksetConflictGroup"] = conflicts[:6]
        decision["reason"] = "parallel_or_batch_write_set_conflict"
        decision["repairSuggestion"] = "Split conflicting tasks into a dependency chain, assign a single owner, or narrow writeSet before auto-dispatch."

    return decisions


def _flatten_snapshot_values(snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    values: list[str] = []
    for key in (
        "agentClass",
        "domainTags",
        "artifactCapabilities",
        "operationCapabilities",
        "runtimeAffinities",
        "toolExposurePolicy",
        "executionSuitability",
        "externalWorkerSuitability",
    ):
        value = snapshot.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(_as_string_list(value))
        elif isinstance(value, dict):
            values.append(_stringify_context(value))
        elif value not in (None, ""):
            values.append(str(value).strip())
    return [item for item in values if item]


def _score_text_overlap(text: str, task_brief: dict[str, Any], *, heavy_tokens: Iterable[str] | None = None) -> int:
    normalized_text = str(text or "").strip().lower()
    if not normalized_text:
        return 0
    score = 0
    required_capabilities = [cap.lower() for cap in _as_string_list(task_brief.get("requiredCapabilities"))]
    for capability in required_capabilities:
        if capability in normalized_text:
            score += 8
        else:
            cap_tokens = _tokenize(capability)
            if cap_tokens and all(token in normalized_text for token in cap_tokens):
                score += 6
    query_tokens = _tokenize(task_brief_query_text(task_brief))
    for token in query_tokens:
        if len(token) < 3:
            continue
        if token in normalized_text:
            score += 1
    for token in list(heavy_tokens or []):
        if token and token in normalized_text:
            score += 4
    return score


def _lower_set(values: Iterable[Any] | None) -> set[str]:
    return {str(item or "").strip().lower() for item in list(values or []) if str(item or "").strip()}


def _snapshot_value_set(snapshot: dict[str, Any] | None, key: str) -> set[str]:
    if not isinstance(snapshot, dict):
        return set()
    value = snapshot.get(key)
    if isinstance(value, dict):
        return _lower_set(value.keys()) | _lower_set(value.values())
    if isinstance(value, (list, tuple, set)):
        return _lower_set(value)
    if value in (None, ""):
        return set()
    return {str(value).strip().lower()}


def _field_overlap(task_values: Iterable[Any] | None, snapshot_values: Iterable[Any] | None) -> set[str]:
    task_set = _lower_set(task_values)
    snapshot_set = _lower_set(snapshot_values)
    if not task_set or not snapshot_set:
        return set()
    matches: set[str] = set()
    for task_value in task_set:
        task_tokens = set(_tokenize(task_value))
        for snapshot_value in snapshot_set:
            if task_value == snapshot_value or task_value in snapshot_value or snapshot_value in task_value:
                matches.add(task_value)
                continue
            snapshot_tokens = set(_tokenize(snapshot_value))
            if task_tokens and snapshot_tokens and task_tokens.issubset(snapshot_tokens):
                matches.add(task_value)
    return matches


def score_capability_candidate(
    *,
    task_brief: dict[str, Any],
    candidate_id: str,
    candidate_label: str,
    description: str = "",
    capability_snapshot: dict[str, Any] | None = None,
    candidate_kind: str = "subagent",
) -> dict[str, Any]:
    snapshot = capability_snapshot if isinstance(capability_snapshot, dict) else {}
    signals: list[str] = []
    score = 0

    required = _as_string_list(task_brief.get("requiredCapabilities"))
    required_set = _lower_set(required)
    domain = _snapshot_value_set(snapshot, "domainTags")
    artifacts = _snapshot_value_set(snapshot, "artifactCapabilities")
    operations = _snapshot_value_set(snapshot, "operationCapabilities")
    runtimes = _snapshot_value_set(snapshot, "runtimeAffinities")
    agent_class = str(snapshot.get("agentClass") or "").strip().lower()
    capsule_mode = str(engineering_task_capsule(task_brief).get("executionMode") or "").strip().lower()
    write_execution = bool(capsule_mode == "write" or task_brief.get("writeRequired") or task_brief.get("writeSet"))

    for key, values, weight in (
        ("domain", domain, 6),
        ("artifact", artifacts, 7),
        ("operation", operations, 8),
        ("runtime", runtimes, 3),
    ):
        matches = _field_overlap(required_set, values)
        if matches:
            score += weight * len(matches)
            signals.append(f"{key}:{','.join(sorted(matches)[:4])}")

    behavior = _lower_set(task_brief.get("behaviorScope"))
    if behavior:
        matches = _field_overlap(behavior, operations | domain | runtimes)
        if matches:
            score += 4 * len(matches)
            signals.append(f"behavior:{','.join(sorted(matches)[:4])}")

    write_set = _lower_set(task_brief.get("writeSet"))
    if write_set:
        matches = _field_overlap(write_set, artifacts | domain)
        if matches:
            score += 3 * len(matches)
            signals.append(f"writeSet:{','.join(sorted(matches)[:4])}")

    if agent_class:
        if candidate_kind == "external_worker" and agent_class == "external_worker":
            score += 3
            signals.append("agentClass:external_worker")
        elif candidate_kind == "subagent" and agent_class != "external_worker":
            score += 2
            signals.append(f"agentClass:{agent_class}")

    if write_execution and candidate_kind == "subagent":
        if agent_class in {"executor", "implementer"}:
            score += 12
            signals.append("writeExecution:executor")
        elif agent_class in {"reviewer", "verifier", "tester", "researcher", "research_coordinator"}:
            score -= 16
            signals.append(f"writeExecution:incompatible:{agent_class}")

    suitability_key = "externalWorkerSuitability" if candidate_kind == "external_worker" else "executionSuitability"
    suitability = str(snapshot.get(suitability_key) or "").strip().lower()
    if suitability in {"high", "preferred", "strong"}:
        score += 4
        signals.append(f"{suitability_key}:high")
    elif suitability in {"medium", "normal"}:
        score += 1
        signals.append(f"{suitability_key}:medium")
    elif suitability in {"low", "avoid"}:
        score -= 4
        signals.append(f"{suitability_key}:{suitability}")

    engineering_role = infer_engineering_task_role(task_brief)
    if engineering_role:
        role_text = " ".join(
            [
                agent_class,
                " ".join(sorted(domain)),
                " ".join(sorted(artifacts)),
                " ".join(sorted(operations)),
                " ".join(_tokenize(candidate_label)),
                " ".join(_tokenize(description)),
            ]
        )
        role_terms = _ROLE_SIGNAL_GROUPS.get(engineering_role, set())
        if any(term in role_text for term in role_terms):
            score += 5
            signals.append(f"engineeringRole:{engineering_role}")
        elif engineering_role in {"review", "verification"} and agent_class in {"reviewer", "verifier", "tester", "architect"}:
            score += 4
            signals.append(f"engineeringRole:{engineering_role}")

    heavy_tokens = _tokenize(" ".join(required))
    text_parts = [
        candidate_id,
        candidate_label,
        description,
        summarize_capability_snapshot(snapshot),
        " ".join(_flatten_snapshot_values(snapshot)),
    ]
    lexical_score = _score_text_overlap(
        "\n".join(str(part or "").strip() for part in text_parts if str(part or "").strip()),
        task_brief,
        heavy_tokens=heavy_tokens,
    )
    if lexical_score:
        score += min(lexical_score, 12)
        signals.append(f"lexical:{min(lexical_score, 12)}")

    confidence = max(0.0, min(1.0, round(score / 24, 2)))
    reason = "no_match"
    if score >= 12:
        reason = "strong_capability_match"
    elif score >= 6:
        reason = "moderate_capability_match"
    elif score >= 4:
        reason = "weak_capability_match"

    return {
        "candidateId": candidate_id,
        "candidateLabel": candidate_label,
        "candidateKind": candidate_kind,
        "score": score,
        "confidence": confidence,
        "reason": reason,
        "matchSignals": signals[:8],
    }


def summarize_capability_snapshot(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict) or not snapshot:
        return ""
    parts: list[str] = []
    agent_class = str(snapshot.get("agentClass") or "").strip()
    if agent_class:
        parts.append(agent_class)
    for key in ("domainTags", "artifactCapabilities", "operationCapabilities", "runtimeAffinities"):
        values = _as_string_list(snapshot.get(key))
        if values:
            parts.append(",".join(values[:4]))
    return " | ".join(parts)


def _candidate_specialist_family(agent: dict[str, Any] | None) -> str:
    if not isinstance(agent, dict):
        return ""
    snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
    return normalize_specialist_family_id(
        snapshot.get("specialistFamily")
        or snapshot.get("family")
        or agent.get("specialistFamily")
        or agent.get("family")
    )


def reveal_subagent_family(family: str, agents: Iterable[dict[str, Any]], *, limit: int = 50) -> dict[str, Any]:
    target_family = normalize_specialist_family_id(family) if str(family or "").strip() else ""
    normalized_agents = [
        agent for agent in list(agents or [])
        if isinstance(agent, dict)
        and str(agent.get("id") or "").strip()
        and str(agent.get("id") or "").strip() != "supervisor"
        and agent.get("isEnabled") is not False
    ]
    family_agents = [
        agent for agent in normalized_agents
        if _candidate_specialist_family(agent).lower() == target_family.lower()
    ] if target_family else []
    members: list[dict[str, Any]] = []
    for agent in family_agents[: max(1, min(int(limit or 50), 100))]:
        snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
        members.append(
            {
                "agentId": str(agent.get("id") or "").strip(),
                "name": str(agent.get("name") or agent.get("id") or "").strip(),
                "description": str(agent.get("description") or "").strip()[:240],
                "family": _candidate_specialist_family(agent),
                "globalExposure": bool(agent.get("globalExposure")),
                "capabilitySnapshot": {
                    "agentClass": snapshot.get("agentClass"),
                    "domainTags": _normalize_scope_values(snapshot.get("domainTags"))[:8],
                    "artifactCapabilities": _normalize_scope_values(snapshot.get("artifactCapabilities"))[:8],
                    "operationCapabilities": _normalize_scope_values(snapshot.get("operationCapabilities"))[:8],
                    "runtimeAffinities": _normalize_scope_values(snapshot.get("runtimeAffinities"))[:8],
                    "runtimeBindings": normalize_subagent_runtime_bindings(snapshot.get("runtimeBindings"))[:4],
                    "executionSuitability": snapshot.get("executionSuitability"),
                },
                "capabilitySummary": summarize_capability_snapshot(snapshot),
            }
        )
    suggested_capabilities: list[str] = []
    for member in members:
        snapshot = member.get("capabilitySnapshot") if isinstance(member.get("capabilitySnapshot"), dict) else {}
        for key in ("domainTags", "artifactCapabilities", "operationCapabilities"):
            for value in _normalize_scope_values(snapshot.get(key)):
                if value not in suggested_capabilities:
                    suggested_capabilities.append(value)
    return {
        "family": target_family,
        "found": bool(members),
        "memberCount": len(family_agents),
        "members": members,
        "suggestedRequiredCapabilities": suggested_capabilities[:12],
        "selectionRule": "Dispatch with familyHint plus requiredCapabilities. Bound research/creative-media subagents receive their runtime tools automatically; unbound custom subagents stay on baseline tools unless the task explicitly grants more.",
    }


def choose_best_local_agent_with_diagnostics(task_brief: dict[str, Any], agents: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    preferred_id = str(task_brief.get("preferredAgentId") or "").strip()
    normalized_agents = [agent for agent in list(agents or []) if isinstance(agent, dict) and str(agent.get("id") or "").strip() and str(agent.get("id") or "").strip() != "supervisor"]
    capsule_mode = str(engineering_task_capsule(task_brief).get("executionMode") or "").strip().lower()
    write_execution = bool(capsule_mode == "write" or task_brief.get("writeRequired") or task_brief.get("writeSet"))
    ignored_preferred_signal = ""
    if preferred_id:
        preferred_agent = None
        for agent in normalized_agents:
            if str(agent.get("id") or "").strip() == preferred_id:
                preferred_agent = agent
                break
        if preferred_agent is None:
            return None, {
                "selectionReason": "preferredAgentId_not_found",
                "selectionConfidence": 0.0,
                "matchSignals": [f"preferredAgentId:{preferred_id}"],
                "targetId": preferred_id,
            }
        snapshot = preferred_agent.get("capabilitySnapshot") if isinstance(preferred_agent.get("capabilitySnapshot"), dict) else {}
        agent_class = str(snapshot.get("agentClass") or "").strip().lower()
        if write_execution and agent_class in {"reviewer", "verifier", "tester", "researcher", "research_coordinator"}:
            ignored_preferred_signal = f"preferredAgentId_incompatible_with_write:{preferred_id}"
        else:
            return preferred_agent, {
                    "selectionReason": "preferredAgentId",
                    "selectionConfidence": 1.0,
                    "matchSignals": [f"preferredAgentId:{preferred_id}"],
                    "targetId": preferred_id,
                }

    raw_family_hint = str(task_brief.get("familyHint") or "").strip()
    family_hint = normalize_specialist_family_id(raw_family_hint, default="") if raw_family_hint else ""
    if family_hint:
        filtered_agents = [
            agent for agent in normalized_agents
            if _candidate_specialist_family(agent).lower() == family_hint.lower()
        ]
        if not filtered_agents:
            return None, {
                "selectionReason": "familyHint_no_matching_subagent",
                "selectionConfidence": 0.0,
                "matchSignals": [f"familyHint:{family_hint}"],
                "targetFamily": family_hint,
            }
        normalized_agents = filtered_agents

    best_agent: dict[str, Any] | None = None
    best_diagnostics: dict[str, Any] = {}
    for agent in normalized_agents:
        if agent.get("isEnabled") is False:
            continue
        snapshot = agent.get("capabilitySnapshot") if isinstance(agent.get("capabilitySnapshot"), dict) else {}
        diagnostics = score_capability_candidate(
            task_brief=task_brief,
            candidate_id=str(agent.get("id") or "").strip(),
            candidate_label=str(agent.get("name") or agent.get("id") or "").strip(),
            description=str(agent.get("description") or "").strip(),
            capability_snapshot=snapshot,
            candidate_kind="subagent",
        )
        if int(diagnostics.get("score") or 0) > int(best_diagnostics.get("score") or 0):
            best_agent = agent
            best_diagnostics = diagnostics
    if int(best_diagnostics.get("score") or 0) < 4:
        diagnostics = best_diagnostics or {
            "selectionReason": "no_matching_subagent",
            "selectionConfidence": 0.0,
            "matchSignals": [],
        }
        if family_hint:
            diagnostics = dict(diagnostics)
            diagnostics["targetFamily"] = family_hint
            diagnostics["matchSignals"] = [f"familyHint:{family_hint}", *list(diagnostics.get("matchSignals") or [])]
        if ignored_preferred_signal:
            diagnostics = dict(diagnostics)
            diagnostics["matchSignals"] = [ignored_preferred_signal, *list(diagnostics.get("matchSignals") or [])]
        return None, diagnostics
    return best_agent, {
        "selectionReason": best_diagnostics.get("reason") or "capability_match",
        "selectionConfidence": best_diagnostics.get("confidence") or 0.0,
        "matchSignals": ([ignored_preferred_signal] if ignored_preferred_signal else [])
        + ([f"familyHint:{family_hint}"] if family_hint else [])
        + list(best_diagnostics.get("matchSignals") or []),
        "targetId": best_diagnostics.get("candidateId"),
        **({"targetFamily": family_hint} if family_hint else {}),
    }


def choose_best_local_agent(task_brief: dict[str, Any], agents: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    agent, _diagnostics = choose_best_local_agent_with_diagnostics(task_brief, agents)
    return agent


_CLAUDE_CODE_WORKER_ID = "claude-code-worker"
_CLAUDE_CODE_RENDERER = "claude_code"
_CLAUDE_CODE_COMMAND_TEMPLATE = (
    'claude -p --permission-mode acceptEdits --output-format text '
    '"V8 external worker task. Read task brief JSON from file: '
    ".v8-agent-os/external-workers/{task_brief_id}/task_brief.json. "
    "Obey writeSet, behaviorScope, requiredCapabilities, and acceptanceContract. "
    "Work only in the current workspace. "
    "When finished, print exactly one <V8_WORKER_RESULT> JSON object with keys "
    "status, summary, changedFiles, commandsRun, verification, and notes </V8_WORKER_RESULT> block.\""
)
_LEGACY_CLAUDE_CODE_COMMAND_TEMPLATE_TOKEN = "Decode this taskBrief base64 JSON: {task_brief_b64}"
_EXTERNAL_WORKER_RESULT_MARKERS = ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"]


def _claude_code_external_worker_descriptor() -> dict[str, Any]:
    return {
        "id": _CLAUDE_CODE_WORKER_ID,
        "name": "Claude Code Worker",
        "description": "Real Claude Code CLI worker for bounded implementation, debugging, review, or verification tasks.",
        "enabled": False,
        "workerType": "claude_code",
        "capabilitySnapshot": {
            "agentClass": "external_worker",
            "domainTags": ["software_engineering", "implementation", "debugging", "code_review"],
            "artifactCapabilities": ["code", "patch", "report"],
            "operationCapabilities": ["implement", "debug", "review", "verify"],
            "runtimeAffinities": ["chat", "command_session", "claude_code"],
            "toolExposurePolicy": "task_brief_driven",
            "externalWorkerSuitability": "high",
        },
        "launchProfile": {
            "commandTemplate": _CLAUDE_CODE_COMMAND_TEMPLATE,
            "renderer": _CLAUDE_CODE_RENDERER,
            "commandProfile": "chat_cli",
            "permissionMode": "acceptEdits",
            "cwdPolicy": "inherit_workspace",
            "envPassThrough": [],
            "startupTimeoutSeconds": 10,
        },
        "sessionMode": "print",
        "allowedSideEffects": ["workspace_write", "tool_use", "long_running_cli"],
        "resultSchema": {
            "type": "v8_worker_result_v1",
            "markers": list(_EXTERNAL_WORKER_RESULT_MARKERS),
        },
    }


def default_external_worker_descriptors() -> list[dict[str, Any]]:
    return [
        _claude_code_external_worker_descriptor(),
    ]


def normalize_external_worker_descriptor(value: Any) -> dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    snapshot = payload.get("capabilitySnapshot") if isinstance(payload.get("capabilitySnapshot"), dict) else {}
    launch_profile = payload.get("launchProfile") if isinstance(payload.get("launchProfile"), dict) else {}
    result_schema = payload.get("resultSchema") if isinstance(payload.get("resultSchema"), dict) else {}
    return {
        "id": str(payload.get("id") or "").strip(),
        "name": str(payload.get("name") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
        "enabled": bool(payload.get("enabled")),
        "workerType": str(payload.get("workerType") or "").strip(),
        "capabilitySnapshot": snapshot,
        "launchProfile": {
            "commandTemplate": str(launch_profile.get("commandTemplate") or "").strip(),
            "renderer": str(launch_profile.get("renderer") or "").strip(),
            "commandProfile": str(launch_profile.get("commandProfile") or "auto").strip() or "auto",
            "permissionMode": str(launch_profile.get("permissionMode") or "acceptEdits").strip() or "acceptEdits",
            "cwdPolicy": str(launch_profile.get("cwdPolicy") or "inherit_workspace").strip() or "inherit_workspace",
            "envPassThrough": _as_string_list(launch_profile.get("envPassThrough")),
            "startupTimeoutSeconds": max(3, min(int(launch_profile.get("startupTimeoutSeconds") or 10), 120)),
        },
        "sessionMode": str(payload.get("sessionMode") or "print").strip() or "print",
        "allowedSideEffects": _as_string_list(payload.get("allowedSideEffects")),
        "resultSchema": {
            "type": str(result_schema.get("type") or "v8_worker_result_v1").strip() or "v8_worker_result_v1",
            "markers": _as_string_list(result_schema.get("markers") or _EXTERNAL_WORKER_RESULT_MARKERS),
        },
    }


def normalize_external_worker_descriptors(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for descriptor in list(values or []):
        normalized = normalize_external_worker_descriptor(descriptor)
        descriptor_id = str(normalized.get("id") or "").strip()
        if not descriptor_id or descriptor_id in seen:
            continue
        seen.add(descriptor_id)
        items.append(normalized)
    if _CLAUDE_CODE_WORKER_ID not in seen:
        items.append(_claude_code_external_worker_descriptor())
    return items


def _prefix_external_worker_command_with_cwd(command: str, *, cwd_policy: str, workspace_path: str) -> str:
    normalized_policy = str(cwd_policy or "").strip()
    normalized_workspace = str(workspace_path or "").strip()
    if normalized_policy != "inherit_workspace" or not normalized_workspace:
        return command
    if sys.platform == "win32":
        quoted = '"' + normalized_workspace.replace('"', '""') + '"'
        return f"cd /d {quoted} && {command}"
    return f"cd {shlex.quote(normalized_workspace)} && {command}"


def external_worker_command_profile(descriptor: dict[str, Any]) -> str:
    launch_profile = descriptor.get("launchProfile") if isinstance(descriptor.get("launchProfile"), dict) else {}
    explicit = str(launch_profile.get("commandProfile") or "").strip().lower()
    if explicit in {"auto", "chat_cli", "shell"}:
        return explicit
    if str(descriptor.get("workerType") or "").strip().lower() == "claude_code":
        return "chat_cli"
    return "auto"


def choose_best_external_worker_with_diagnostics(task_brief: dict[str, Any], descriptors: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    preferred_worker_type = str(task_brief.get("preferredWorkerType") or "").strip().lower()
    normalized = [item for item in normalize_external_worker_descriptors(descriptors) if item.get("enabled")]
    if preferred_worker_type:
        for descriptor in normalized:
            worker_type = str(descriptor.get("workerType") or descriptor.get("id") or "").strip().lower()
            if worker_type == preferred_worker_type or str(descriptor.get("id") or "").strip().lower() == preferred_worker_type:
                return descriptor, {
                    "selectionReason": "preferredWorkerType",
                    "selectionConfidence": 1.0,
                    "matchSignals": [f"preferredWorkerType:{preferred_worker_type}"],
                    "targetId": str(descriptor.get("id") or "").strip(),
                }
        return None, {
            "selectionReason": "preferredWorkerType_not_found",
            "selectionConfidence": 0.0,
            "matchSignals": [f"preferredWorkerType:{preferred_worker_type}"],
            "targetId": preferred_worker_type,
        }
    best_descriptor: dict[str, Any] | None = None
    best_diagnostics: dict[str, Any] = {}
    for descriptor in normalized:
        snapshot = descriptor.get("capabilitySnapshot") if isinstance(descriptor.get("capabilitySnapshot"), dict) else {}
        diagnostics = score_capability_candidate(
            task_brief=task_brief,
            candidate_id=str(descriptor.get("id") or "").strip(),
            candidate_label=str(descriptor.get("name") or descriptor.get("id") or "").strip(),
            description=" ".join(
                part
                for part in [
                    str(descriptor.get("description") or "").strip(),
                    str(descriptor.get("workerType") or "").strip(),
                ]
                if part
            ),
            capability_snapshot=snapshot,
            candidate_kind="external_worker",
        )
        if int(diagnostics.get("score") or 0) > int(best_diagnostics.get("score") or 0):
            best_descriptor = descriptor
            best_diagnostics = diagnostics
    if int(best_diagnostics.get("score") or 0) < 4:
        return None, best_diagnostics or {
            "selectionReason": "no_matching_external_worker",
            "selectionConfidence": 0.0,
            "matchSignals": [],
        }
    return best_descriptor, {
        "selectionReason": best_diagnostics.get("reason") or "capability_match",
        "selectionConfidence": best_diagnostics.get("confidence") or 0.0,
        "matchSignals": list(best_diagnostics.get("matchSignals") or []),
        "targetId": best_diagnostics.get("candidateId"),
    }


def choose_best_external_worker(task_brief: dict[str, Any], descriptors: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    worker, _diagnostics = choose_best_external_worker_with_diagnostics(task_brief, descriptors)
    return worker


def _safe_external_worker_task_id(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return normalized[:80] or "task"


def _shell_double_quoted_arg(value: str) -> str:
    normalized = str(value or "")
    if sys.platform == "win32":
        return '"' + normalized.replace('"', r'\"') + '"'
    return shlex.quote(normalized)


def _write_external_worker_task_brief(
    *,
    task_brief: dict[str, Any],
    workspace_path: str,
) -> tuple[str, str]:
    workspace = Path(str(workspace_path or "").strip() or os.getcwd()).resolve()
    safe_task_id = _safe_external_worker_task_id(task_brief.get("taskBriefId"))
    relative_path = Path(".v8-agent-os") / "external-workers" / safe_task_id / "task_brief.json"
    brief_path = workspace / relative_path
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "v8.external_worker.task_brief.v1",
        "taskBrief": task_brief,
        "resultContract": {
            "requiredMarkers": list(_EXTERNAL_WORKER_RESULT_MARKERS),
            "requiredKeys": ["status", "summary", "changedFiles", "commandsRun", "verification", "notes"],
            "successRule": "Only the JSON block between the V8 markers is accepted by V8OS.",
        },
        "executionRules": [
            "Work only inside the current workspace unless the task brief explicitly allows otherwise.",
            "Respect writeSet, behaviorScope, requiredCapabilities, and acceptanceContract.",
            "Do not print secrets or provider API keys.",
            "When done, print exactly one V8_WORKER_RESULT JSON block and no second result block.",
            "Keep the result JSON compact: one line, short string values, no Markdown fence.",
        ],
    }
    brief_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(brief_path), str(relative_path).replace("\\", "/")


def _external_worker_workspace_preflight(
    workspace_path: str,
    *,
    workspace_id: str = "",
    project_id: str = "",
) -> tuple[bool, str, dict[str, Any] | None]:
    from core.workspace_capability import build_workspace_binding, workspace_side_effect_block_payload

    binding = build_workspace_binding(
        {
            "runtime_kind": "chat",
            "workspace_path": str(workspace_path or "").strip() or None,
            "workspace_id": str(workspace_id or "").strip() or None,
            "project_id": str(project_id or "").strip() or None,
        },
        runtime_kind="chat",
    )
    if binding.side_effects_allowed:
        return True, str(binding.active_workspace_root), None
    return False, "", workspace_side_effect_block_payload(
        binding,
        operation="external_worker",
        subject=str(workspace_path or ""),
    )


def _render_claude_code_worker_command(
    *,
    descriptor: dict[str, Any],
    task_brief: dict[str, Any],
    workspace_path: str,
    workspace_id: str = "",
    project_id: str = "",
) -> str:
    launch_profile = descriptor.get("launchProfile") if isinstance(descriptor.get("launchProfile"), dict) else {}
    preflight_ok, effective_workspace_path, block_payload = _external_worker_workspace_preflight(
        workspace_path,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if not preflight_ok:
        return json.dumps(block_payload or {"ok": False, "kind": "workspace_side_effect_blocked"}, ensure_ascii=False)
    permission_mode = str(launch_profile.get("permissionMode") or "acceptEdits").strip() or "acceptEdits"
    _brief_path, relative_brief_path = _write_external_worker_task_brief(
        task_brief=task_brief,
        workspace_path=effective_workspace_path,
    )
    instruction = (
        f"V8 external worker task. Read task brief JSON from file: {relative_brief_path}. "
        "Use Claude Code tools as needed to inspect and edit files. "
        "Respect writeSet, behaviorScope, requiredCapabilities, and acceptanceContract. "
        "Do not leave the workspace unless the task brief explicitly allows it. "
        "When finished, print exactly one <V8_WORKER_RESULT> JSON object with keys "
        "status, summary, changedFiles, commandsRun, verification, and notes </V8_WORKER_RESULT> block. "
        "The result JSON must be compact one-line JSON with short string values and no Markdown fence. "
        "If blocked, still print the marker block with status blocked and explain why."
    )
    command = (
        "claude -p "
        f"--permission-mode {_shell_double_quoted_arg(permission_mode)} "
        "--output-format text "
        f"{_shell_double_quoted_arg(instruction)}"
    )
    return _prefix_external_worker_command_with_cwd(
        command,
        cwd_policy=str(launch_profile.get("cwdPolicy") or "").strip(),
        workspace_path=effective_workspace_path,
    )


def _uses_claude_code_dedicated_renderer(descriptor: dict[str, Any]) -> bool:
    launch_profile = descriptor.get("launchProfile") if isinstance(descriptor.get("launchProfile"), dict) else {}
    renderer = str(launch_profile.get("renderer") or "").strip().lower()
    worker_type = str(descriptor.get("workerType") or "").strip().lower()
    command_template = str(launch_profile.get("commandTemplate") or "").strip()
    if renderer == _CLAUDE_CODE_RENDERER:
        return True
    if worker_type != "claude_code":
        return False
    if not command_template:
        return True
    return _LEGACY_CLAUDE_CODE_COMMAND_TEMPLATE_TOKEN in command_template


def render_external_worker_command(
    *,
    descriptor: dict[str, Any],
    task_brief: dict[str, Any],
    workspace_path: str = "",
    workspace_id: str = "",
    project_id: str = "",
) -> str:
    launch_profile = descriptor.get("launchProfile") if isinstance(descriptor.get("launchProfile"), dict) else {}
    if _uses_claude_code_dedicated_renderer(descriptor):
        return _render_claude_code_worker_command(
            descriptor=descriptor,
            task_brief=task_brief,
            workspace_path=workspace_path,
            workspace_id=workspace_id,
            project_id=project_id,
        )
    preflight_ok, effective_workspace_path, block_payload = _external_worker_workspace_preflight(
        workspace_path,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if not preflight_ok:
        return json.dumps(block_payload or {"ok": False, "kind": "workspace_side_effect_blocked"}, ensure_ascii=False)
    command_template = str(launch_profile.get("commandTemplate") or "").strip()
    if not command_template:
        return ""
    task_brief_json = json.dumps(task_brief, ensure_ascii=False)
    context_text = _stringify_context(task_brief.get("context"))
    acceptance_text = _stringify_context(task_brief.get("acceptanceContract"))
    replacements = {
        "goal": str(task_brief.get("goal") or ""),
        "context": context_text,
        "write_set": json.dumps(list(task_brief.get("writeSet") or []), ensure_ascii=False),
        "behavior_scope": json.dumps(list(task_brief.get("behaviorScope") or []), ensure_ascii=False),
        "required_capabilities": json.dumps(list(task_brief.get("requiredCapabilities") or []), ensure_ascii=False),
        "acceptance_contract": acceptance_text,
        "task_brief_id": str(task_brief.get("taskBriefId") or ""),
        "parallel_group": str(task_brief.get("parallelGroup") or ""),
        "task_brief_json": task_brief_json,
        "task_brief_b64": base64.b64encode(task_brief_json.encode("utf-8")).decode("ascii"),
        "workspace_path": effective_workspace_path,
    }

    class _SafeDict(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        rendered = command_template.format_map(_SafeDict(replacements)).strip()
    except Exception:
        rendered = command_template.strip()
    return _prefix_external_worker_command_with_cwd(
        rendered,
        cwd_policy=str(launch_profile.get("cwdPolicy") or "").strip(),
        workspace_path=effective_workspace_path,
    )


def parse_external_worker_result_block(
    value: Any,
    *,
    markers: Iterable[Any] | None = None,
) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    marker_values = [str(item).strip() for item in list(markers or []) if str(item).strip()]
    start_marker = marker_values[0] if len(marker_values) >= 1 else "<V8_WORKER_RESULT>"
    end_marker = marker_values[1] if len(marker_values) >= 2 else "</V8_WORKER_RESULT>"

    def _parse(candidate: str) -> dict[str, Any] | None:
        end = candidate.rfind(end_marker)
        start = candidate.rfind(start_marker, 0, end) if end >= 0 else -1
        if start < 0 or end < 0:
            return None
        body = candidate[start + len(start_marker):end].strip()
        if not body:
            return None
        try:
            parsed_value = json.loads(body)
        except Exception:
            return None
        return parsed_value if isinstance(parsed_value, dict) else None

    compact_text = re.sub(r"[\r\n]+", "", text)
    return _parse(compact_text) or _parse(text)


def make_external_delegation_id(*, command_id: str, task_brief_id: str, worker_id: str) -> str:
    return f"external::{command_id}::{task_brief_id or 'task'}::{worker_id}"


def make_local_delegation_id(*, invocation_id: str, branch_index: int, task_brief_id: str, agent_id: str) -> str:
    return f"subagent::{invocation_id}::{branch_index}::{task_brief_id or 'task'}::{agent_id}"


def parse_delegation_id(delegation_id: str) -> dict[str, str]:
    normalized = str(delegation_id or "").strip()
    if not normalized:
        return {}
    parts = normalized.split("::")
    if len(parts) >= 4 and parts[0] == "external":
        return {
            "lane": "external_worker",
            "commandId": parts[1],
            "taskBriefId": parts[2],
            "targetId": parts[3],
        }
    if len(parts) >= 5 and parts[0] == "subagent":
        return {
            "lane": "subagent",
            "invocationId": parts[1],
            "branchIndex": parts[2],
            "taskBriefId": parts[3],
            "targetId": parts[4],
        }
    return {"lane": "", "raw": normalized}


def compact_external_worker_registry_entry(descriptor: dict[str, Any]) -> dict[str, Any]:
    snapshot = descriptor.get("capabilitySnapshot") if isinstance(descriptor.get("capabilitySnapshot"), dict) else {}
    return {
        "id": str(descriptor.get("id") or "").strip(),
        "name": str(descriptor.get("name") or "").strip(),
        "description": str(descriptor.get("description") or "").strip(),
        "enabled": bool(descriptor.get("enabled")),
        "workerType": str(descriptor.get("workerType") or "").strip(),
        "capabilitySnapshot": deepcopy(snapshot),
    }
