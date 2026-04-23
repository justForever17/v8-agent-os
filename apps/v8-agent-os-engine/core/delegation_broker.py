from __future__ import annotations

import base64
import json
import re
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


def default_external_worker_descriptors() -> list[dict[str, Any]]:
    return [
        {
            "id": "coding-cli-worker",
            "name": "Coding CLI Worker",
            "description": "External coding worker template for bounded implementation, debug, or verification tasks.",
            "enabled": False,
            "workerType": "coding_cli",
            "capabilitySnapshot": {
                "agentClass": "external_worker",
                "domainTags": ["software_engineering", "implementation", "verification"],
                "artifactCapabilities": ["code", "patch"],
                "operationCapabilities": ["implement", "debug", "verify"],
                "runtimeAffinities": ["chat", "command_session"],
                "toolExposurePolicy": "task_brief_driven",
                "externalWorkerSuitability": "high",
            },
            "launchProfile": {
                "commandTemplate": "",
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
        },
        {
            "id": "research-writer-worker",
            "name": "Research / Writing Worker",
            "description": "External research and writing worker template for synthesis, drafting, or evidence gathering tasks.",
            "enabled": False,
            "workerType": "research_writer",
            "capabilitySnapshot": {
                "agentClass": "external_worker",
                "domainTags": ["research", "writing", "analysis"],
                "artifactCapabilities": ["report", "draft"],
                "operationCapabilities": ["research", "synthesize", "write"],
                "runtimeAffinities": ["chat", "command_session"],
                "toolExposurePolicy": "task_brief_driven",
                "externalWorkerSuitability": "high",
            },
            "launchProfile": {
                "commandTemplate": "",
                "cwdPolicy": "inherit_workspace",
                "envPassThrough": [],
                "startupTimeoutSeconds": 10,
            },
            "sessionMode": "interactive",
            "allowedSideEffects": ["workspace_write", "network_access", "long_running_cli"],
            "resultSchema": {
                "type": "v8_worker_result_v1",
                "markers": ["<V8_WORKER_RESULT>", "</V8_WORKER_RESULT>"],
            },
        },
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
    return items


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
        return command_template.format_map(_SafeDict(replacements)).strip()
    except Exception:
        return command_template.strip()


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
