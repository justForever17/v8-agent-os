from __future__ import annotations

import base64
import json
import re
import shlex
import sys
from copy import deepcopy
from typing import Any, Iterable


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


def _default_task_brief(index: int = 0) -> dict[str, Any]:
    return {
        "taskBriefId": f"task-{index + 1}",
        "goal": "",
        "context": "",
        "routeQuery": "",
        "writeSet": [],
        "behaviorScope": [],
        "requiredCapabilities": [],
        "runtimeAccess": [],
        "acceptanceContract": "",
        "dependency": [],
        "parallelGroup": "",
        "executionLaneHint": "auto",
        "preferredAgentId": "",
        "preferredWorkerType": "",
    }


def normalize_task_brief(value: Any, *, index: int = 0) -> dict[str, Any]:
    payload = dict(value or {}) if isinstance(value, dict) else {}
    defaults = _default_task_brief(index)
    normalized = {
        "taskBriefId": str(payload.get("taskBriefId") or payload.get("task_brief_id") or defaults["taskBriefId"]).strip(),
        "goal": str(payload.get("goal") or "").strip(),
        "context": payload.get("context") if isinstance(payload.get("context"), dict) else str(payload.get("context") or "").strip(),
        "routeQuery": str(payload.get("routeQuery") or payload.get("route_query") or payload.get("extensionsRouteQuery") or payload.get("extensions_route_query") or "").strip(),
        "writeSet": _normalize_scope_values(payload.get("writeSet") or payload.get("write_set")),
        "behaviorScope": _normalize_scope_values(payload.get("behaviorScope") or payload.get("behavior_scope")),
        "requiredCapabilities": _normalize_scope_values(payload.get("requiredCapabilities") or payload.get("required_capabilities")),
        "runtimeAccess": _normalize_scope_values(payload.get("runtimeAccess") or payload.get("runtime_access")),
        "acceptanceContract": payload.get("acceptanceContract") if isinstance(payload.get("acceptanceContract"), dict) else str(payload.get("acceptanceContract") or payload.get("acceptance_contract") or "").strip(),
        "dependency": _normalize_scope_values(payload.get("dependency")),
        "parallelGroup": str(payload.get("parallelGroup") or payload.get("parallel_group") or "").strip(),
        "executionLaneHint": str(payload.get("executionLaneHint") or payload.get("execution_lane_hint") or "auto").strip().lower() or "auto",
        "preferredAgentId": str(payload.get("preferredAgentId") or payload.get("preferred_agent_id") or "").strip(),
        "preferredWorkerType": str(payload.get("preferredWorkerType") or payload.get("preferred_worker_type") or "").strip(),
    }
    for key in ("criticalFiles", "readSet", "verificationMatrix", "proofExpectations"):
        normalized[key] = _normalize_scope_values(payload.get(key) or payload.get(key[0].lower() + key[1:]))
    if isinstance(payload.get("engineeringTaskCapsule"), dict):
        normalized["engineeringTaskCapsule"] = dict(payload.get("engineeringTaskCapsule") or {})
    elif isinstance(payload.get("engineering_task_capsule"), dict):
        normalized["engineeringTaskCapsule"] = dict(payload.get("engineering_task_capsule") or {})
    if normalized["executionLaneHint"] not in {"subagent", "external_worker", "auto"}:
        normalized["executionLaneHint"] = "auto"
    if not normalized["taskBriefId"]:
        normalized["taskBriefId"] = defaults["taskBriefId"]
    return normalized


def normalize_task_briefs(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [normalize_task_brief(value, index=index) for index, value in enumerate(list(values or []))]


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
    """Evaluate Engineering Lane write-set safety before broker dispatch."""
    normalized_tasks = [normalize_task_brief(item, index=index) for index, item in enumerate(list(task_briefs or []))]
    decisions: list[dict[str, Any]] = []
    write_owners: list[tuple[int, str, str]] = []
    source = str(decision_source or ("planner_auto" if auto_dispatch else "supervisor_manual")).strip() or (
        "planner_auto" if auto_dispatch else "supervisor_manual"
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

        if engineering_like:
            if write_set:
                risk = "within_write_set"
                reason = "declared_write_set_present"
            elif read_only_safe:
                risk = "read_only_safe"
                reason = "read_only_or_doc_only_task_without_write_set"
            else:
                risk = "missing_write_set"
                reason = "engineering_task_missing_write_set"
                repair = "Repair planner output with a concrete writeSet, or mark the task read-only/review-only before auto-dispatch."
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
        "plannerSuitability",
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

    suitability_key = "externalWorkerSuitability" if candidate_kind == "external_worker" else "plannerSuitability"
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


def choose_best_local_agent_with_diagnostics(task_brief: dict[str, Any], agents: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    preferred_id = str(task_brief.get("preferredAgentId") or "").strip()
    normalized_agents = [agent for agent in list(agents or []) if isinstance(agent, dict) and str(agent.get("id") or "").strip() and str(agent.get("id") or "").strip() != "supervisor"]
    if preferred_id:
        for agent in normalized_agents:
            if str(agent.get("id") or "").strip() == preferred_id:
                return agent, {
                    "selectionReason": "preferredAgentId",
                    "selectionConfidence": 1.0,
                    "matchSignals": [f"preferredAgentId:{preferred_id}"],
                    "targetId": preferred_id,
                }
        return None, {
            "selectionReason": "preferredAgentId_not_found",
            "selectionConfidence": 0.0,
            "matchSignals": [f"preferredAgentId:{preferred_id}"],
            "targetId": preferred_id,
        }

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
        return None, best_diagnostics or {
            "selectionReason": "no_matching_subagent",
            "selectionConfidence": 0.0,
            "matchSignals": [],
        }
    return best_agent, {
        "selectionReason": best_diagnostics.get("reason") or "capability_match",
        "selectionConfidence": best_diagnostics.get("confidence") or 0.0,
        "matchSignals": list(best_diagnostics.get("matchSignals") or []),
        "targetId": best_diagnostics.get("candidateId"),
    }


def choose_best_local_agent(task_brief: dict[str, Any], agents: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    agent, _diagnostics = choose_best_local_agent_with_diagnostics(task_brief, agents)
    return agent


_CLAUDE_CODE_WORKER_ID = "claude-code-worker"
_CLAUDE_CODE_COMMAND_TEMPLATE = (
    'claude -p --permission-mode acceptEdits --output-format text '
    '"V8 external worker task. Decode this taskBrief base64 JSON: {task_brief_b64}. '
    "Obey writeSet, behaviorScope, requiredCapabilities, and acceptanceContract. "
    "Work only in the current workspace. "
    "When finished, print exactly one <V8_WORKER_RESULT> JSON object with keys "
    "summary, localSelfCheck, artifactRefs, and acceptanceHint </V8_WORKER_RESULT> block.\""
)


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
            "cwdPolicy": "inherit_workspace",
            "envPassThrough": [],
            "startupTimeoutSeconds": 10,
        },
        "sessionMode": "interactive",
        "allowedSideEffects": ["workspace_write", "tool_use", "long_running_cli"],
        "resultSchema": {
            "type": "v8_worker_result_v1",
            "markers": ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
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
            "cwdPolicy": str(launch_profile.get("cwdPolicy") or "inherit_workspace").strip() or "inherit_workspace",
            "envPassThrough": _as_string_list(launch_profile.get("envPassThrough")),
            "startupTimeoutSeconds": max(3, min(int(launch_profile.get("startupTimeoutSeconds") or 10), 120)),
        },
        "sessionMode": str(payload.get("sessionMode") or "interactive").strip() or "interactive",
        "allowedSideEffects": _as_string_list(payload.get("allowedSideEffects")),
        "resultSchema": {
            "type": str(result_schema.get("type") or "v8_worker_result_v1").strip() or "v8_worker_result_v1",
            "markers": _as_string_list(result_schema.get("markers") or ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"]),
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


def render_external_worker_command(
    *,
    descriptor: dict[str, Any],
    task_brief: dict[str, Any],
    workspace_path: str = "",
) -> str:
    launch_profile = descriptor.get("launchProfile") if isinstance(descriptor.get("launchProfile"), dict) else {}
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
        "workspace_path": workspace_path,
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
        workspace_path=workspace_path,
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
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        return None
    body = text[start + len(start_marker):end].strip()
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


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
