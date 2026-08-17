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
        flattened: list[Any] = []
        for item in value:
            if isinstance(item, str):
                raw = item.strip()
                if raw.startswith("[") and raw.endswith("]"):
                    try:
                        decoded = json.loads(raw)
                    except Exception:
                        decoded = None
                    if isinstance(decoded, list):
                        flattened.extend(decoded)
                        continue
            flattened.append(item)
        return _unique_str_list(flattened)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.startswith("[") and normalized.endswith("]"):
            try:
                decoded = json.loads(normalized)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                return _normalize_scope_values(decoded)
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


_TASK_BRIEF_WORKER_OVERRIDE_ALIASES: dict[str, tuple[str, ...]] = {
    "schemaVersion": ("schemaVersion", "schema_version"),
    "routeQuery": ("routeQuery", "route_query", "extensionsRouteQuery", "extensions_route_query"),
    "writeSet": ("writeSet", "write_set"),
    "readSet": ("readSet", "read_set"),
    "criticalFiles": ("criticalFiles", "critical_files"),
    "expectedOutputs": ("expectedOutputs", "expected_outputs", "expectedOutput", "expected_output"),
    "expectedArtifacts": ("expectedArtifacts", "expected_artifacts"),
    "constraints": ("constraints", "constraint", "boundaries", "boundary"),
    "behaviorScope": ("behaviorScope", "behavior_scope"),
    "requiredCapabilities": ("requiredCapabilities", "required_capabilities"),
    "runtimeAccess": ("runtimeAccess", "runtime_access"),
    "toolPolicy": ("toolPolicy", "tool_policy"),
    "allowedTools": ("allowedTools", "allowed_tools"),
    "forbiddenTools": ("forbiddenTools", "forbidden_tools"),
    "noTools": ("noTools", "no_tools"),
    "pluginReferences": ("pluginReferences", "plugin_references"),
    "evidenceRefs": ("evidenceRefs", "evidence_refs"),
    "detailRefs": ("detailRefs", "detail_refs"),
    "specRefs": ("specRefs", "spec_refs"),
    "acceptanceContract": ("acceptanceContract", "acceptance_contract", "acceptance"),
    "acceptanceTiers": ("acceptanceTiers", "acceptance_tiers"),
    "dependencies": ("dependencies", "dependency"),
    "sideEffectPolicy": ("sideEffectPolicy", "side_effect_policy"),
    "budget": ("budget", "executionBudget", "execution_budget"),
    "failurePolicy": ("failurePolicy", "failure_policy"),
    "parallelGroup": ("parallelGroup", "parallel_group"),
    "executionLaneHint": ("executionLaneHint", "execution_lane_hint"),
    "familyHint": ("familyHint", "family_hint", "specialistFamily"),
    "targetAgentName": ("targetAgentName", "target_agent_name"),
    "preferredAgentId": ("preferredAgentId", "preferred_agent_id"),
    "preferredWorkerType": ("preferredWorkerType", "preferred_worker_type"),
    "researchRefs": ("researchRefs", "research_refs"),
    "verificationMatrix": ("verificationMatrix", "verification_matrix"),
    "proofExpectations": ("proofExpectations", "proof_expectations"),
    "engineeringTaskCapsule": ("engineeringTaskCapsule", "engineering_task_capsule"),
    "deliverableKind": ("deliverableKind", "deliverable_kind"),
    "writeRequired": ("writeRequired", "write_required"),
    "readOnly": ("readOnly", "read_only"),
    "delegationDepth": ("delegationDepth", "delegation_depth"),
    "verificationEvidenceContract": (
        "verificationEvidenceContract",
        "verification_evidence_contract",
    ),
    "validateSkillArtifact": ("validateSkillArtifact", "validate_skill_artifact"),
    "requiredSkillContracts": ("requiredSkillContracts", "required_skill_contracts"),
    "delegationPolicy": ("delegationPolicy", "delegation_policy"),
    "allowChildDelegation": (
        "allowChildDelegation",
        "allow_child_delegation",
        "allowNestedDelegation",
        "allow_nested_delegation",
    ),
    "requireChildDelegation": (
        "requireChildDelegation",
        "require_child_delegation",
        "childDelegationRequired",
        "child_delegation_required",
    ),
    "childDelegationBudget": (
        "childDelegationBudget",
        "child_delegation_budget",
        "childBudget",
        "child_budget",
    ),
    "writeSetPartitions": (
        "writeSetPartitions",
        "write_set_partitions",
        "writePartitions",
        "write_partitions",
    ),
    "parentTaskBriefId": ("parentTaskBriefId", "parent_task_brief_id"),
    "siblingIndex": ("siblingIndex", "sibling_index"),
    "siblingCount": ("siblingCount", "sibling_count"),
}

_TASK_BRIEF_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    **_TASK_BRIEF_WORKER_OVERRIDE_ALIASES,
    "taskBriefId": ("taskBriefId", "task_brief_id"),
    "goal": ("goal",),
    "context": ("context",),
    "targetCount": (
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
    "workerBriefs": (
        "workerBriefs",
        "worker_briefs",
        "workers",
        "branches",
        "parallelBranches",
        "parallel_branches",
    ),
    "fanoutReason": (
        "fanoutReason",
        "fanout_reason",
        "parallelismReason",
        "parallelism_reason",
    ),
    "childDelegationPolicyExplicit": ("childDelegationPolicyExplicit",),
    "unsupportedFields": ("unsupportedFields", "unsupported_fields"),
}

_DELEGATION_POLICY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "allowChildDelegation": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["allowChildDelegation"],
    "requireChildDelegation": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["requireChildDelegation"],
    "childDelegationBudget": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["childDelegationBudget"],
    "writeSetPartitions": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["writeSetPartitions"],
}
_TOOL_POLICY_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "allowedTools": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["allowedTools"],
    "forbiddenTools": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["forbiddenTools"],
    "noTools": _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["noTools"],
}
_ENGINEERING_CAPSULE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "taskId": ("taskId", "task_id"),
    "workspacePath": ("workspacePath", "workspace_path"),
    "executionMode": ("executionMode", "execution_mode"),
    "writeRequired": ("writeRequired", "write_required"),
    "readOnly": ("readOnly", "read_only"),
    "writeSet": ("writeSet", "write_set"),
    "readSet": ("readSet", "read_set"),
    "allowedTools": ("allowedTools", "allowed_tools"),
    "forbiddenTools": ("forbiddenTools", "forbidden_tools"),
    "expectedArtifacts": ("expectedArtifacts", "expected_artifacts"),
    "verificationContract": ("verificationContract", "verification_contract"),
    "proofExpectations": ("proofExpectations", "proof_expectations"),
}
_ENGINEERING_CAPSULE_CROSS_LAYER_AUTHORITY_FIELDS = frozenset(
    {
        "executionMode",
        "writeRequired",
        "readOnly",
        "writeSet",
        "readSet",
        "allowedTools",
        "forbiddenTools",
    }
)
_DELEGATION_POLICY_INPUT_FIELDS = frozenset(
    alias
    for aliases in _DELEGATION_POLICY_FIELD_ALIASES.values()
    for alias in aliases
)


def _task_brief_first_present(payload: dict[str, Any], field: str) -> Any:
    return _first_present(payload, _TASK_BRIEF_FIELD_ALIASES.get(field, (field,)))


def _alias_conflict_diagnostics(
    payload: dict[str, Any],
    *,
    index: int,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for field, aliases in _TASK_BRIEF_FIELD_ALIASES.items():
        present = [alias for alias in aliases if alias in payload]
        if len(present) < 2:
            continue
        first_value = payload.get(present[0])
        if all(payload.get(alias) == first_value for alias in present[1:]):
            continue
        diagnostics.append(
            {
                "code": "task_brief_alias_conflict",
                "index": index,
                "taskBriefId": str(
                    _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                    or ""
                ).strip(),
                "field": field,
                "aliases": present,
            }
        )

    raw_policy = _task_brief_first_present(payload, "delegationPolicy")
    policy = dict(raw_policy) if isinstance(raw_policy, dict) else {}
    for field, aliases in _DELEGATION_POLICY_FIELD_ALIASES.items():
        present = [alias for alias in aliases if alias in policy]
        if len(present) < 2:
            continue
        first_value = policy.get(present[0])
        if all(policy.get(alias) == first_value for alias in present[1:]):
            continue
        diagnostics.append(
            {
                "code": "task_brief_alias_conflict",
                "index": index,
                "taskBriefId": str(
                    _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                    or ""
                ).strip(),
                "field": f"delegationPolicy.{field}",
                "aliases": [f"delegationPolicy.{alias}" for alias in present],
            }
        )

    for field, aliases in _DELEGATION_POLICY_FIELD_ALIASES.items():
        top_present = [alias for alias in aliases if alias in payload]
        nested_present = [alias for alias in aliases if alias in policy]
        if not top_present or not nested_present:
            continue
        top_value = payload.get(top_present[0])
        nested_value = policy.get(nested_present[0])
        if top_value == nested_value:
            continue
        diagnostics.append(
            {
                "code": "task_brief_alias_conflict",
                "index": index,
                "taskBriefId": str(
                    _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                    or ""
                ).strip(),
                "field": f"delegationPolicy.{field}",
                "aliases": [top_present[0], f"delegationPolicy.{nested_present[0]}"],
            }
        )

    raw_tool_policy = _task_brief_first_present(payload, "toolPolicy")
    tool_policy = dict(raw_tool_policy) if isinstance(raw_tool_policy, dict) else {}
    for field, aliases in _TOOL_POLICY_FIELD_ALIASES.items():
        nested_present = [alias for alias in aliases if alias in tool_policy]
        if len(nested_present) > 1:
            first_value = tool_policy.get(nested_present[0])
            if any(tool_policy.get(alias) != first_value for alias in nested_present[1:]):
                diagnostics.append(
                    {
                        "code": "task_brief_alias_conflict",
                        "index": index,
                        "taskBriefId": str(
                            _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                            or ""
                        ).strip(),
                        "field": f"toolPolicy.{field}",
                        "aliases": [f"toolPolicy.{alias}" for alias in nested_present],
                    }
                )
        top_present = [alias for alias in aliases if alias in payload]
        if top_present and nested_present:
            top_value = payload.get(top_present[0])
            nested_value = tool_policy.get(nested_present[0])
            if top_value != nested_value:
                diagnostics.append(
                    {
                        "code": "task_brief_alias_conflict",
                        "index": index,
                        "taskBriefId": str(
                            _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                            or ""
                        ).strip(),
                        "field": f"toolPolicy.{field}",
                        "aliases": [top_present[0], f"toolPolicy.{nested_present[0]}"],
                    }
                )
    raw_capsule = _task_brief_first_present(payload, "engineeringTaskCapsule")
    capsule = dict(raw_capsule) if isinstance(raw_capsule, dict) else {}
    for field, aliases in _ENGINEERING_CAPSULE_FIELD_ALIASES.items():
        nested_present = [alias for alias in aliases if alias in capsule]
        if len(nested_present) > 1:
            first_value = capsule.get(nested_present[0])
            if any(capsule.get(alias) != first_value for alias in nested_present[1:]):
                diagnostics.append(
                    {
                        "code": "task_brief_alias_conflict",
                        "index": index,
                        "taskBriefId": str(
                            _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                            or ""
                        ).strip(),
                        "field": f"engineeringTaskCapsule.{field}",
                        "aliases": [
                            f"engineeringTaskCapsule.{alias}" for alias in nested_present
                        ],
                    }
                )
        if field not in _ENGINEERING_CAPSULE_CROSS_LAYER_AUTHORITY_FIELDS:
            continue
        top_aliases = _TASK_BRIEF_FIELD_ALIASES.get(field, ())
        top_present = [alias for alias in top_aliases if alias in payload]
        if top_present and nested_present:
            if payload.get(top_present[0]) != capsule.get(nested_present[0]):
                diagnostics.append(
                    {
                        "code": "task_brief_alias_conflict",
                        "index": index,
                        "taskBriefId": str(
                            _first_present(payload, _TASK_BRIEF_FIELD_ALIASES["taskBriefId"])
                            or ""
                        ).strip(),
                        "field": f"engineeringTaskCapsule.{field}",
                        "aliases": [
                            top_present[0],
                            f"engineeringTaskCapsule.{nested_present[0]}",
                        ],
                    }
                )
    return diagnostics


def task_brief_contract_diagnostics(values: Iterable[Any] | None) -> dict[str, Any]:
    """Describe identity/alias defects without rewriting or dropping a brief."""

    tasks = list(values or [])
    alias_conflicts: list[dict[str, Any]] = []
    indexes_by_id: dict[str, list[int]] = {}
    task_ids_by_index: dict[int, str] = {}
    dependency_refs_by_index: dict[int, list[str]] = {}
    for index, value in enumerate(tasks):
        if not isinstance(value, dict):
            continue
        alias_conflicts.extend(_alias_conflict_diagnostics(value, index=index))
        task_id = str(_task_brief_first_present(value, "taskBriefId") or "").strip()
        if task_id:
            indexes_by_id.setdefault(task_id, []).append(index)
            task_ids_by_index[index] = task_id
        dependency_refs_by_index[index] = _normalize_scope_values(
            _task_brief_first_present(value, "dependencies")
        )
        raw_workers = _task_brief_first_present(value, "workerBriefs")
        if isinstance(raw_workers, list):
            for worker_index, worker in enumerate(raw_workers):
                if not isinstance(worker, dict):
                    continue
                for diagnostic in _alias_conflict_diagnostics(
                    worker,
                    index=worker_index,
                ):
                    alias_conflicts.append(
                        {
                            **diagnostic,
                            "parentIndex": index,
                            "parentTaskBriefId": task_id,
                            "workerIndex": worker_index,
                        }
                    )

    duplicates: list[dict[str, Any]] = []
    for task_id, indexes in indexes_by_id.items():
        if len(indexes) < 2:
            continue
        dependent_indexes = [
            index
            for index, dependencies in dependency_refs_by_index.items()
            if task_id in dependencies
        ]
        duplicates.append(
            {
                "taskBriefId": task_id,
                "indexes": indexes,
                "dependentIndexes": dependent_indexes,
                "dependentTaskBriefIds": [
                    task_ids_by_index.get(index, "")
                    for index in dependent_indexes
                ],
            }
        )
    return {
        "aliasConflicts": alias_conflicts,
        "duplicateTaskBriefIds": duplicates,
    }

_TASK_BRIEF_COMPAT_INPUT_FIELDS = frozenset(
    {
        alias
        for aliases in _TASK_BRIEF_WORKER_OVERRIDE_ALIASES.values()
        for alias in aliases
    }
    | {
        "task_brief_id",
        "extensionsRouteQuery",
        "extensions_route_query",
        "acceptance",
        "tieredAcceptance",
        "tiered_acceptance",
        "boundaries",
        "boundary",
        "specialistFamily",
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
        "worker_briefs",
        "workers",
        "branches",
        "parallelBranches",
        "parallel_branches",
        "fanout_reason",
        "parallelismReason",
        "parallelism_reason",
        "delegationPolicy",
        "delegation_policy",
        "allow_child_delegation",
        "allowNestedDelegation",
        "allow_nested_delegation",
        "require_child_delegation",
        "childDelegationRequired",
        "child_delegation_required",
        "child_delegation_budget",
        "childBudget",
        "child_budget",
        "write_set_partitions",
        "writePartitions",
        "write_partitions",
        "deliverable_kind",
        "write_required",
        "read_only",
        "delegation_depth",
        "verification_evidence_contract",
        "validate_skill_artifact",
        "required_skill_contracts",
        "extensions",
        "unsupportedFields",
        "unsupported_fields",
        "contractDiagnostics",
    }
)


def _task_brief_extensions(
    payload: dict[str, Any],
    *,
    canonical_fields: Iterable[str],
) -> tuple[dict[str, Any], list[str]]:
    """Preserve forward fields without granting them canonical semantics."""

    existing = payload.get("extensions")
    extensions = deepcopy(existing) if isinstance(existing, dict) else {}
    known_fields = set(canonical_fields) | set(_TASK_BRIEF_COMPAT_INPUT_FIELDS)
    unsupported = set(
        _normalize_scope_values(
            _task_brief_first_present(payload, "unsupportedFields")
        )
    )
    unsupported.update(str(key) for key in extensions)
    for key, value in payload.items():
        if key in known_fields:
            continue
        extensions[key] = deepcopy(value)
        unsupported.add(str(key))
    return extensions, sorted(unsupported)


_SHARED_CONTEXT_AUTHORITY_KEYS = frozenset(
    {
        "engineeringexecutioncontract",
        "engineeringtaskcapsule",
        "inheritedengineeringcontract",
        "handoffcontract",
        "creativemediaexecutioncontract",
        "canvasexecutioncontract",
        "specexecutionbundle",
        "stagecontent",
        "assignedtaskdetails",
        "assignedtasksummaries",
        "parentcontext",
        "workercontext",
        "ephemeralmirror",
        "toolpolicy",
        "allowedtools",
        "forbiddentools",
        "notools",
        "runtimeaccess",
        "pluginreferences",
        "plugingrants",
        "plugincomponentgrants",
        "extensiontoolpolicy",
        "readset",
        "writeset",
        "criticalfiles",
        "writesetpartitions",
        "sideeffectpolicy",
        "delegationpolicy",
        "allowchilddelegation",
        "requirechilddelegation",
        "childdelegationbudget",
        "workspacepath",
        "originalworkspacepath",
        "shelldialect",
        "workspacelease",
        "sandboxlease",
        "capabilitygrants",
        "sessionid",
        "runid",
        "userid",
        "rawref",
        "rawreasoning",
        "rawtoolpayload",
        "providerpayload",
    }
)
_SHARED_CONTEXT_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "auth",
        "authentication",
        "authorization",
        "authorizationheader",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passphrase",
        "privatekey",
        "secret",
        "token",
        "apikey",
        "accesstoken",
        "authtoken",
        "bearertoken",
        "clientsecret",
        "idtoken",
        "refreshtoken",
        "secretkey",
        "sessiontoken",
    }
)
_SHARED_CONTEXT_SENSITIVE_SUFFIXES = (
    "apikey",
    "accesstoken",
    "authtoken",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "idtoken",
    "password",
    "passphrase",
    "privatekey",
    "refreshtoken",
    "secret",
    "secretkey",
    "sessiontoken",
    "token",
)


def _shared_context_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _shared_context_sensitive_key(value: str) -> bool:
    return value in _SHARED_CONTEXT_SENSITIVE_EXACT_KEYS or any(
        value.endswith(suffix) for suffix in _SHARED_CONTEXT_SENSITIVE_SUFFIXES
    )


def _filtered_shared_context(value: Any) -> Any:
    """Keep shared facts visible without projecting authority or private runtime state."""

    if isinstance(value, dict):
        filtered: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = _shared_context_key(key)
            if normalized_key in _SHARED_CONTEXT_AUTHORITY_KEYS:
                continue
            if _shared_context_sensitive_key(normalized_key):
                filtered[str(key)] = "<redacted>"
                continue
            next_value = _filtered_shared_context(item)
            if next_value not in (None, "", [], {}):
                filtered[str(key)] = next_value
        return filtered
    if isinstance(value, (list, tuple)):
        filtered_items = [_filtered_shared_context(item) for item in value]
        return [item for item in filtered_items if item not in (None, "", [], {})]
    if isinstance(value, set):
        filtered_items = [_filtered_shared_context(item) for item in value]
        return sorted(
            (item for item in filtered_items if item not in (None, "", [], {})),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_target_count(value: Any, *, default: int = 1, maximum: int = 1000) -> int:
    try:
        count = int(value)
    except Exception:
        count = default
    return max(1, min(count, maximum))


def _safe_nonnegative_int(value: Any, *, default: int = 0, maximum: int = 1000) -> int:
    try:
        count = int(value)
    except Exception:
        count = default
    return max(0, min(count, maximum))


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


_REQUIRED_CHILD_DELEGATION_RE = re.compile(
    r"(?:孙\s*(?:agent|代理|智能体)|grandchild|child\s+agent|nested\s+delegation)",
    re.IGNORECASE,
)

_FORBIDDEN_CHILD_DELEGATION_RE = re.compile(
    r"(?:不得|禁止|不要|无需|不需要|不允许|不能|不可)[^\n，。；;]{0,48}"
    r"(?:孙\s*(?:agent|代理|智能体)|grandchild|child\s+agent|nested\s+delegation)"
    r"|(?:do\s+not|must\s+not|cannot|can't|may\s+not|without|forbid(?:den)?|prohibit(?:ed)?|disallow(?:ed)?)"
    r"[^\n.;]{0,64}(?:grandchild|child\s+agent|nested\s+delegation)",
    re.IGNORECASE,
)


def acceptance_requires_child_delegation(value: Any) -> bool:
    """Return true only when a must-level acceptance item names a child layer.

    This is contract interpretation, not task-shape guessing: optional prose,
    family hints, and the general ability to delegate never make delegation
    mandatory.
    """

    tiers = _normalize_acceptance_tiers(value)
    must_items = [str(item or "").strip() for item in tiers.get("must") or []]
    return any(
        _REQUIRED_CHILD_DELEGATION_RE.search(item)
        and not _FORBIDDEN_CHILD_DELEGATION_RE.search(item)
        for item in must_items
        if item
    )


def task_brief_requires_child_delegation(task_brief: dict[str, Any] | None) -> bool:
    if not isinstance(task_brief, dict):
        return False
    explicit = _first_present(
        task_brief,
        (
            "requireChildDelegation",
            "require_child_delegation",
            "childDelegationRequired",
            "child_delegation_required",
        ),
    )
    if explicit is not None:
        return _safe_bool(explicit)
    acceptance = task_brief.get("acceptanceTiers") or task_brief.get("acceptance_tiers")
    if acceptance is None:
        acceptance = task_brief.get("acceptanceContract") or task_brief.get("acceptance_contract")
    return acceptance_requires_child_delegation(acceptance)


def _default_task_brief(index: int = 0) -> dict[str, Any]:
    return {
        "schemaVersion": "",
        "taskBriefId": f"task-{index + 1}",
        "goal": "",
        "context": "",
        "routeQuery": "",
        "readSet": [],
        "writeSet": [],
        "expectedOutputs": [],
        "expectedArtifacts": [],
        "constraints": [],
        "behaviorScope": [],
        "requiredCapabilities": [],
        "runtimeAccess": [],
        "toolPolicy": {"mode": "default", "allowedTools": [], "forbiddenTools": []},
        "allowedTools": [],
        "forbiddenTools": [],
        "pluginReferences": [],
        "evidenceRefs": [],
        "detailRefs": [],
        "specRefs": [],
        "acceptanceContract": "",
        "acceptanceTiers": {"must": [], "should": [], "nice": []},
        "dependencies": [],
        "dependency": [],
        "sideEffectPolicy": {},
        "budget": {},
        "failurePolicy": {},
        "parallelGroup": "",
        "executionLaneHint": "auto",
        "familyHint": "",
        "targetAgentName": "",
        "preferredAgentId": "",
        "preferredWorkerType": "",
        "researchRefs": [],
        "targetCount": 1,
        "workerBriefs": [],
        "fanoutReason": "",
        "allowChildDelegation": False,
        "requireChildDelegation": False,
        "childDelegationPolicyExplicit": False,
        "childDelegationBudget": {},
        "writeSetPartitions": [],
        "parentTaskBriefId": "",
        "siblingIndex": 0,
        "siblingCount": 0,
    }


def normalize_task_brief(value: Any, *, index: int = 0) -> dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    defaults = _default_task_brief(index)
    worker_briefs = _normalize_worker_briefs(
        _task_brief_first_present(payload, "workerBriefs")
    )
    target_count = _safe_target_count(
        _task_brief_first_present(payload, "targetCount")
    )
    if worker_briefs:
        target_count = max(target_count, len(worker_briefs))
    raw_delegation_policy = _task_brief_first_present(payload, "delegationPolicy")
    delegation_policy = dict(raw_delegation_policy or {}) if isinstance(raw_delegation_policy, dict) else {}
    child_policy_keys = (
        "allowChildDelegation",
        "allow_child_delegation",
        "allowNestedDelegation",
        "allow_nested_delegation",
    )
    if "childDelegationPolicyExplicit" in payload:
        child_policy_explicit = _safe_bool(
            _task_brief_first_present(payload, "childDelegationPolicyExplicit")
        )
    else:
        child_policy_explicit = any(key in payload for key in child_policy_keys) or any(
            key in delegation_policy for key in child_policy_keys
        )
    child_policy_value = _first_present(payload, child_policy_keys)
    if child_policy_value is None:
        child_policy_value = _first_present(delegation_policy, child_policy_keys)
    child_delegation_budget = _task_brief_first_present(payload, "childDelegationBudget")
    if child_delegation_budget is None:
        child_delegation_budget = _first_present(
            delegation_policy,
            ("childDelegationBudget", "child_delegation_budget", "childBudget", "child_budget"),
        )
    write_set_partitions = _task_brief_first_present(payload, "writeSetPartitions")
    if write_set_partitions is None:
        write_set_partitions = _first_present(
            delegation_policy,
            _DELEGATION_POLICY_FIELD_ALIASES["writeSetPartitions"],
        )
    acceptance_contract = _task_brief_first_present(payload, "acceptanceContract")
    acceptance_tiers = _first_present(
        payload,
        ("acceptanceTiers", "acceptance_tiers", "tieredAcceptance", "tiered_acceptance"),
    )
    normalized_acceptance_tiers = _normalize_acceptance_tiers(acceptance_tiers if acceptance_tiers is not None else acceptance_contract)
    child_requirement_value = _first_present(
        payload,
        (
            "requireChildDelegation",
            "require_child_delegation",
            "childDelegationRequired",
            "child_delegation_required",
        ),
    )
    if child_requirement_value is None:
        child_requirement_value = _first_present(
            delegation_policy,
            _DELEGATION_POLICY_FIELD_ALIASES["requireChildDelegation"],
        )
    child_delegation_required = (
        _safe_bool(child_requirement_value)
        if child_requirement_value is not None
        else acceptance_requires_child_delegation(normalized_acceptance_tiers)
    )
    # A must-level child-verification contract is itself authority to use the
    # default one-layer delegation path unless the caller explicitly forbade
    # it.  Persisting ``required=true`` beside an implicit ``allowed=false``
    # leaves the prompt renderer to repair a contradictory contract later and
    # makes durable Runtime episodes misleading to operators and recovery
    # code.
    child_delegation_allowed = _safe_bool(child_policy_value)
    if child_delegation_required and not child_policy_explicit:
        child_delegation_allowed = True
        if not isinstance(child_delegation_budget, dict) or not child_delegation_budget:
            child_delegation_budget = {"maxChildren": 1, "maxDepth": 1}
    raw_tool_policy = _task_brief_first_present(payload, "toolPolicy")
    tool_policy = dict(raw_tool_policy or {}) if isinstance(raw_tool_policy, dict) else {}
    allowed_tools_present = any(key in payload for key in ("allowedTools", "allowed_tools")) or any(
        key in tool_policy for key in ("allowedTools", "allowed_tools")
    )
    allowed_tools = _normalize_scope_values(
        _task_brief_first_present(payload, "allowedTools")
        if any(key in payload for key in ("allowedTools", "allowed_tools"))
        else _first_present(tool_policy, ("allowedTools", "allowed_tools"))
    )
    forbidden_tools = _normalize_scope_values(
        _task_brief_first_present(payload, "forbiddenTools")
        if any(key in payload for key in ("forbiddenTools", "forbidden_tools"))
        else _first_present(tool_policy, ("forbiddenTools", "forbidden_tools"))
    )
    no_tools = _safe_bool(
        _task_brief_first_present(payload, "noTools")
        if any(key in payload for key in ("noTools", "no_tools"))
        else _first_present(tool_policy, ("noTools", "no_tools"))
    )
    tool_policy_mode = str(tool_policy.get("mode") or "").strip().lower()
    if no_tools or tool_policy_mode == "none":
        tool_policy_mode = "none"
        allowed_tools = []
    elif tool_policy_mode not in {"default", "allowlist", "none"}:
        tool_policy_mode = "allowlist" if allowed_tools_present else "default"
    constraints = _normalize_scope_values(
        _task_brief_first_present(payload, "constraints")
    )
    behavior_scope = _normalize_scope_values(
        _task_brief_first_present(payload, "behaviorScope")
    )
    for boundary in constraints:
        if boundary not in behavior_scope:
            behavior_scope.append(boundary)
    expected_artifacts = _normalize_scope_values(
        _task_brief_first_present(payload, "expectedArtifacts")
    )
    expected_outputs = _normalize_scope_values(
        _task_brief_first_present(payload, "expectedOutputs")
    )
    if not expected_outputs and expected_artifacts:
        # Historical callers used expectedArtifacts as the only output field.
        # Preserve that minimum compatibility without erasing the distinction:
        # expectedOutputs describes acceptance-facing results, while
        # expectedArtifacts contains concrete artifact paths only.
        expected_outputs = list(expected_artifacts)
    dependencies = _normalize_scope_values(
        _task_brief_first_present(payload, "dependencies")
    )
    spec_refs_value = _task_brief_first_present(payload, "specRefs")
    spec_refs = (
        deepcopy(spec_refs_value)
        if isinstance(spec_refs_value, dict)
        else _normalize_scope_values(spec_refs_value)
    )
    side_effect_policy_value = _task_brief_first_present(payload, "sideEffectPolicy")
    budget_value = _task_brief_first_present(payload, "budget")
    failure_policy_value = _task_brief_first_present(payload, "failurePolicy")
    normalized_tool_policy = deepcopy(tool_policy)
    normalized_tool_policy.update(
        {
            "mode": tool_policy_mode,
            "allowedTools": allowed_tools,
            "forbiddenTools": forbidden_tools,
        }
    )
    normalized_child_budget = (
        dict(child_delegation_budget or {})
        if isinstance(child_delegation_budget, dict)
        else {}
    )
    normalized_write_set_partitions = (
        [
            dict(item) if isinstance(item, dict) else item
            for item in list(write_set_partitions or [])
        ]
        if isinstance(write_set_partitions, list)
        else []
    )
    normalized_delegation_policy = {
        "allowChildDelegation": child_delegation_allowed,
        "requireChildDelegation": child_delegation_required,
        "childDelegationBudget": normalized_child_budget,
        "writeSetPartitions": normalized_write_set_partitions,
    }
    policy_extensions = {
        f"delegationPolicy.{key}": deepcopy(item)
        for key, item in delegation_policy.items()
        if key not in _DELEGATION_POLICY_INPUT_FIELDS
    }
    contract_diagnostics = [
        dict(item)
        for item in list(payload.get("contractDiagnostics") or [])
        if isinstance(item, dict)
    ]
    for diagnostic in _alias_conflict_diagnostics(payload, index=index):
        if diagnostic not in contract_diagnostics:
            contract_diagnostics.append(diagnostic)
    normalized = {
        "schemaVersion": str(
            _task_brief_first_present(payload, "schemaVersion")
            or defaults["schemaVersion"]
        ).strip(),
        "taskBriefId": str(
            _task_brief_first_present(payload, "taskBriefId")
            or defaults["taskBriefId"]
        ).strip(),
        "goal": str(_task_brief_first_present(payload, "goal") or "").strip(),
        "context": (
            _task_brief_first_present(payload, "context")
            if isinstance(_task_brief_first_present(payload, "context"), dict)
            else str(_task_brief_first_present(payload, "context") or "").strip()
        ),
        "routeQuery": str(_task_brief_first_present(payload, "routeQuery") or "").strip(),
        "readSet": _normalize_scope_values(_task_brief_first_present(payload, "readSet")),
        "writeSet": _normalize_scope_values(_task_brief_first_present(payload, "writeSet")),
        "expectedOutputs": expected_outputs,
        "expectedArtifacts": expected_artifacts,
        "constraints": constraints,
        "behaviorScope": behavior_scope,
        "requiredCapabilities": _normalize_scope_values(
            _task_brief_first_present(payload, "requiredCapabilities")
        ),
        "runtimeAccess": _normalize_scope_values(
            _task_brief_first_present(payload, "runtimeAccess")
        ),
        "toolPolicy": normalized_tool_policy,
        "allowedTools": allowed_tools,
        "forbiddenTools": forbidden_tools,
        "pluginReferences": _normalize_plugin_references(
            _task_brief_first_present(payload, "pluginReferences")
        ),
        "evidenceRefs": _normalize_scope_values(
            _task_brief_first_present(payload, "evidenceRefs")
        ),
        "detailRefs": _normalize_scope_values(
            _task_brief_first_present(payload, "detailRefs")
        ),
        "specRefs": spec_refs,
        "acceptanceContract": (
            deepcopy(acceptance_contract)
            if isinstance(acceptance_contract, (dict, list))
            else str(acceptance_contract or "").strip()
        ),
        "acceptanceTiers": normalized_acceptance_tiers,
        "dependencies": list(dependencies),
        # Keep the internal singular spelling until all persisted/runtime readers
        # have migrated. Both keys always carry the same canonical ordering.
        "dependency": list(dependencies),
        "sideEffectPolicy": (
            deepcopy(side_effect_policy_value)
            if isinstance(side_effect_policy_value, dict)
            else {}
        ),
        "budget": deepcopy(budget_value) if isinstance(budget_value, dict) else {},
        "failurePolicy": (
            deepcopy(failure_policy_value)
            if isinstance(failure_policy_value, dict)
            else {}
        ),
        "parallelGroup": str(_task_brief_first_present(payload, "parallelGroup") or "").strip(),
        "executionLaneHint": str(
            _task_brief_first_present(payload, "executionLaneHint") or "auto"
        ).strip().lower() or "auto",
        "familyHint": str(_task_brief_first_present(payload, "familyHint") or "").strip(),
        "targetAgentName": str(
            _task_brief_first_present(payload, "targetAgentName") or ""
        ).strip(),
        "preferredAgentId": str(
            _task_brief_first_present(payload, "preferredAgentId") or ""
        ).strip(),
        "preferredWorkerType": str(
            _task_brief_first_present(payload, "preferredWorkerType") or ""
        ).strip(),
        "researchRefs": _normalize_scope_values(
            _task_brief_first_present(payload, "researchRefs")
        ),
        "targetCount": target_count,
        "workerBriefs": worker_briefs,
        "fanoutReason": str(
            _task_brief_first_present(payload, "fanoutReason") or ""
        ).strip(),
        "allowChildDelegation": child_delegation_allowed,
        "requireChildDelegation": child_delegation_required,
        "childDelegationPolicyExplicit": child_policy_explicit,
        "childDelegationBudget": normalized_child_budget,
        "writeSetPartitions": normalized_write_set_partitions,
        "parentTaskBriefId": str(
            _task_brief_first_present(payload, "parentTaskBriefId")
            or defaults["parentTaskBriefId"]
        ).strip(),
        "siblingIndex": _safe_nonnegative_int(
            _task_brief_first_present(payload, "siblingIndex"),
        ),
        "siblingCount": _safe_nonnegative_int(
            _task_brief_first_present(payload, "siblingCount"),
        ),
    }
    if isinstance(raw_delegation_policy, dict) or child_policy_explicit or child_delegation_required or normalized_child_budget or normalized_write_set_partitions:
        normalized["delegationPolicy"] = normalized_delegation_policy
    if contract_diagnostics:
        normalized["contractDiagnostics"] = contract_diagnostics
    for key in ("criticalFiles", "verificationMatrix", "proofExpectations"):
        normalized[key] = _normalize_scope_values(_task_brief_first_present(payload, key))
    engineering_capsule_value = _task_brief_first_present(
        payload,
        "engineeringTaskCapsule",
    )
    if isinstance(engineering_capsule_value, dict):
        normalized["engineeringTaskCapsule"] = dict(engineering_capsule_value)
    if "deliverableKind" in payload or "deliverable_kind" in payload:
        normalized["deliverableKind"] = str(
            _first_present(payload, ("deliverableKind", "deliverable_kind")) or ""
        ).strip()
    if "writeRequired" in payload or "write_required" in payload:
        normalized["writeRequired"] = _safe_bool(
            _task_brief_first_present(payload, "writeRequired")
        )
    if "readOnly" in payload or "read_only" in payload:
        normalized["readOnly"] = _safe_bool(
            _task_brief_first_present(payload, "readOnly")
        )
    if "delegationDepth" in payload or "delegation_depth" in payload:
        try:
            normalized["delegationDepth"] = max(
                0,
                int(_task_brief_first_present(payload, "delegationDepth") or 0),
            )
        except (TypeError, ValueError):
            pass
    verification_evidence_contract = _task_brief_first_present(
        payload,
        "verificationEvidenceContract",
    )
    if isinstance(verification_evidence_contract, dict):
        normalized["verificationEvidenceContract"] = dict(
            verification_evidence_contract
        )
    if "validateSkillArtifact" in payload or "validate_skill_artifact" in payload:
        normalized["validateSkillArtifact"] = _safe_bool(
            _task_brief_first_present(payload, "validateSkillArtifact")
        )
    if "requiredSkillContracts" in payload or "required_skill_contracts" in payload:
        normalized["requiredSkillContracts"] = _normalize_scope_values(
            _task_brief_first_present(payload, "requiredSkillContracts")
        )
    extensions, unsupported_fields = _task_brief_extensions(
        payload,
        canonical_fields=normalized.keys(),
    )
    extensions.update(policy_extensions)
    unsupported_fields = sorted({*unsupported_fields, *policy_extensions})
    if extensions:
        normalized["extensions"] = extensions
    if unsupported_fields:
        normalized["unsupportedFields"] = unsupported_fields
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
    context: dict[str, Any] = {
        "parentGoal": parent_goal,
        "parallelWorker": {"index": index + 1, "count": count},
    }
    if parent_context not in (None, "", [], {}):
        # Preserve the complete parent payload for runtime recovery while only
        # exposing its non-authority facts to the delegated model.
        context["parentContext"] = deepcopy(parent_context)
        shared_context = _filtered_shared_context(parent_context)
        if shared_context not in (None, "", [], {}):
            context["sharedContext"] = shared_context
    if worker_context not in (None, "", [], {}):
        context["workerContext"] = deepcopy(worker_context)
    return context


_CAPSULE_OVERRIDE_KEYS: dict[str, tuple[str, ...]] = {
    "readSet": ("readSet", "read_set", "mustRead"),
    "writeSet": ("writeSet", "write_set", "allowedWorkset"),
    "criticalFiles": ("criticalFiles", "critical_files"),
    "expectedOutputs": ("expectedOutputs", "expected_outputs"),
    "expectedArtifacts": ("expectedArtifacts", "expected_artifacts"),
    "verificationMatrix": (
        "verificationContract",
        "verification_contract",
        "verificationMatrix",
        "verification_matrix",
    ),
    "proofExpectations": ("proofExpectations", "proof_expectations"),
    "acceptanceContract": ("acceptance", "acceptanceContract", "acceptance_contract"),
    "writeRequired": ("writeRequired", "write_required", "executionMode", "execution_mode"),
    "readOnly": (
        "writeSet",
        "write_set",
        "allowedWorkset",
        "expectedArtifacts",
        "expected_artifacts",
        "writeRequired",
        "write_required",
        "executionMode",
        "execution_mode",
    ),
}


def _remove_aliases(payload: dict[str, Any], aliases: Iterable[str]) -> None:
    for alias in aliases:
        payload.pop(alias, None)


def _worker_override_keys(worker: dict[str, Any]) -> set[str]:
    return {
        canonical_key
        for canonical_key, aliases in _TASK_BRIEF_WORKER_OVERRIDE_ALIASES.items()
        if any(alias in worker for alias in aliases)
    }


def _apply_worker_task_brief_overrides(
    branch_payload: dict[str, Any],
    worker: dict[str, Any],
) -> set[str]:
    """Apply one worker slice without letting normalized parent duplicates win."""

    override_keys = _worker_override_keys(worker)

    if "toolPolicy" in override_keys:
        for key in ("allowedTools", "forbiddenTools", "noTools"):
            _remove_aliases(branch_payload, _TASK_BRIEF_WORKER_OVERRIDE_ALIASES[key])
    else:
        inherited_policy = (
            deepcopy(branch_payload.get("toolPolicy"))
            if isinstance(branch_payload.get("toolPolicy"), dict)
            else {}
        )
        for key in ("allowedTools", "forbiddenTools", "noTools"):
            if key in override_keys:
                _remove_aliases(inherited_policy, _TASK_BRIEF_WORKER_OVERRIDE_ALIASES[key])
        if inherited_policy:
            branch_payload["toolPolicy"] = inherited_policy

    if "delegationPolicy" in override_keys:
        for key in ("allowChildDelegation", "requireChildDelegation", "childDelegationBudget"):
            _remove_aliases(branch_payload, _TASK_BRIEF_WORKER_OVERRIDE_ALIASES[key])
        branch_payload.pop("childDelegationPolicyExplicit", None)
    else:
        inherited_delegation_policy = (
            deepcopy(branch_payload.get("delegationPolicy"))
            if isinstance(branch_payload.get("delegationPolicy"), dict)
            else {}
        )
        for key in ("allowChildDelegation", "requireChildDelegation", "childDelegationBudget"):
            if key in override_keys:
                _remove_aliases(
                    inherited_delegation_policy,
                    _TASK_BRIEF_WORKER_OVERRIDE_ALIASES[key],
                )
        if inherited_delegation_policy:
            branch_payload["delegationPolicy"] = inherited_delegation_policy

    if "engineeringTaskCapsule" in override_keys:
        for key in _CAPSULE_OVERRIDE_KEYS:
            if key not in override_keys:
                _remove_aliases(
                    branch_payload,
                    _TASK_BRIEF_WORKER_OVERRIDE_ALIASES.get(key, (key,)),
                )
    else:
        inherited_capsule = (
            deepcopy(branch_payload.get("engineeringTaskCapsule"))
            if isinstance(branch_payload.get("engineeringTaskCapsule"), dict)
            else {}
        )
        if inherited_capsule:
            inherited_capsule.pop("capsuleId", None)
            inherited_capsule.pop("taskId", None)
            for key in override_keys:
                _remove_aliases(inherited_capsule, _CAPSULE_OVERRIDE_KEYS.get(key, ()))
            if "writeSet" in override_keys and "expectedArtifacts" not in override_keys:
                _remove_aliases(inherited_capsule, _CAPSULE_OVERRIDE_KEYS["expectedArtifacts"])
            if "writeSet" in override_keys:
                _remove_aliases(inherited_capsule, _CAPSULE_OVERRIDE_KEYS["writeRequired"])
            if (
                "writeRequired" in override_keys
                and not _safe_bool(
                    _first_present(
                        worker,
                        _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["writeRequired"],
                    )
                )
            ):
                _remove_aliases(inherited_capsule, _CAPSULE_OVERRIDE_KEYS["readOnly"])
            branch_payload["engineeringTaskCapsule"] = inherited_capsule

    for canonical_key, aliases in _TASK_BRIEF_WORKER_OVERRIDE_ALIASES.items():
        if canonical_key not in override_keys:
            continue
        _remove_aliases(branch_payload, aliases)
        branch_payload[canonical_key] = _first_present(worker, aliases)

    if "writeSet" in override_keys:
        worker_write_set = _normalize_scope_values(branch_payload.get("writeSet"))
        if "expectedArtifacts" not in override_keys:
            branch_payload["expectedArtifacts"] = list(worker_write_set)
        if "writeRequired" not in override_keys:
            branch_payload["writeRequired"] = bool(worker_write_set)

    read_only_override = (
        "readOnly" in override_keys
        and _safe_bool(branch_payload.get("readOnly"))
    )
    write_disabled_override = (
        "writeRequired" in override_keys
        and not _safe_bool(branch_payload.get("writeRequired"))
    )
    if read_only_override or write_disabled_override:
        branch_payload["writeSet"] = []
        branch_payload["expectedArtifacts"] = []
        branch_payload["writeRequired"] = False

    worker_delegation_policy = (
        branch_payload.get("delegationPolicy")
        if isinstance(branch_payload.get("delegationPolicy"), dict)
        else {}
    )
    if "allowChildDelegation" in override_keys or any(
        alias in worker_delegation_policy
        for alias in _TASK_BRIEF_WORKER_OVERRIDE_ALIASES["allowChildDelegation"]
    ):
        branch_payload["childDelegationPolicyExplicit"] = True
    return override_keys


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
            # A normalized macro already contains canonical keys. Canonicalize
            # each worker override as a group so a legacy alias cannot be
            # shadowed by the parent's inherited canonical value or derived
            # Engineering Task Capsule/tool-policy projections.
            _apply_worker_task_brief_overrides(branch_payload, worker)
            worker_extensions, worker_unsupported_fields = _task_brief_extensions(
                worker,
                canonical_fields=branch_payload.keys(),
            )
            if worker_extensions:
                inherited_extensions = (
                    dict(branch_payload.get("extensions") or {})
                    if isinstance(branch_payload.get("extensions"), dict)
                    else {}
                )
                branch_payload["extensions"] = {
                    **inherited_extensions,
                    **worker_extensions,
                }
            if worker_unsupported_fields:
                branch_payload["unsupportedFields"] = sorted(
                    {
                        *_normalize_scope_values(branch_payload.get("unsupportedFields")),
                        *worker_unsupported_fields,
                    }
                )
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


_TASK_QUERY_RUNTIME_ONLY_CONTEXT_KEYS = {
    "stageContent",
    "specExecutionBundle",
    "engineeringExecutionContract",
    "engineering_execution_contract",
    "engineeringTaskCapsule",
    "engineering_task_capsule",
    "inheritedEngineeringContract",
    "inherited_engineering_contract",
    "handoffContract",
    "creativeMediaExecutionContract",
    "creative_media_execution_contract",
    "canvasExecutionContract",
    "canvas_execution_contract",
    "assignedTaskDetails",
    "assignedTaskSummaries",
    "parentContext",
    "workerContext",
    "sharedContext",
    # These fields are rendered by the delegated system contract with explicit
    # authority labels.  Dumping them into the user-like task query makes a
    # sibling goal look like additional work and makes an upstream handoff look
    # like untrusted prose instead of bounded evidence.
    "activeCollaborators",
    "collaborationBoundary",
    "dependencyResults",
}


def _task_query_context(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    visible = {
        key: item
        for key, item in value.items()
        if key not in _TASK_QUERY_RUNTIME_ONLY_CONTEXT_KEYS
    }
    return _filtered_shared_context(visible)


def task_brief_query_text(task_brief: dict[str, Any] | None) -> str:
    if not isinstance(task_brief, dict):
        return ""
    parts: list[str] = []
    goal = str(task_brief.get("goal") or "").strip()
    if goal:
        parts.append(goal)
    context_text = _stringify_context(_task_query_context(task_brief.get("context")))
    if context_text:
        parts.append(f"Context: {context_text}")
    execution_workspace = str(
        task_brief.get("workspacePath")
        or task_brief.get("workspace_path")
        or ""
    ).strip()
    if execution_workspace:
        # Render the governed active workspace as an explicit execution fact.
        # Generic context projection deliberately hides workspace authority
        # fields, while the worker still needs the selected child checkout.
        parts.append(f"Execution workspace: {execution_workspace}")
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
        "selectionRule": "Choose one exact member name, then dispatch with task.targetAgentName. familyHint and capabilities explain the choice but never authorize blind selection. Bound research/creative-media subagents receive their runtime tools automatically; unbound custom subagents stay on baseline tools unless the task explicitly grants more.",
    }


def choose_best_local_agent_with_diagnostics(task_brief: dict[str, Any], agents: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    target_name = str(task_brief.get("targetAgentName") or "").strip()
    preferred_id = str(task_brief.get("preferredAgentId") or "").strip()
    normalized_agents = [agent for agent in list(agents or []) if isinstance(agent, dict) and str(agent.get("id") or "").strip() and str(agent.get("id") or "").strip() != "supervisor"]
    capsule_mode = str(engineering_task_capsule(task_brief).get("executionMode") or "").strip().lower()
    write_execution = bool(capsule_mode == "write" or task_brief.get("writeRequired") or task_brief.get("writeSet"))
    ignored_preferred_signal = ""
    if target_name:
        exact_matches = [
            agent
            for agent in normalized_agents
            if str(agent.get("name") or "").strip().casefold() == target_name.casefold()
            and agent.get("isEnabled") is not False
        ]
        if not exact_matches:
            return None, {
                "selectionReason": "targetAgentName_not_found",
                "selectionConfidence": 0.0,
                "matchSignals": [f"targetAgentName:{target_name}"],
                "targetName": target_name,
            }
        if len(exact_matches) > 1:
            return None, {
                "selectionReason": "targetAgentName_ambiguous",
                "selectionConfidence": 0.0,
                "matchSignals": [f"targetAgentName:{target_name}"],
                "targetName": target_name,
                "candidateIds": [str(agent.get("id") or "").strip() for agent in exact_matches],
            }
        exact_agent = exact_matches[0]
        exact_id = str(exact_agent.get("id") or "").strip()
        if preferred_id and preferred_id != exact_id:
            return None, {
                "selectionReason": "targetAgentName_preferredAgentId_mismatch",
                "selectionConfidence": 0.0,
                "matchSignals": [f"targetAgentName:{target_name}", f"preferredAgentId:{preferred_id}"],
                "targetName": target_name,
                "targetId": exact_id,
            }
        return exact_agent, {
            "selectionReason": "targetAgentName",
            "selectionConfidence": 1.0,
            "matchSignals": [f"targetAgentName:{target_name}"],
            "targetName": target_name,
            "targetId": exact_id,
        }
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
