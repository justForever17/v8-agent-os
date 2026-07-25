from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from core.database import db
from core.delegation_broker import normalize_task_brief, normalize_task_briefs
from core.runtime_episodes import (
    build_runtime_episode,
    emit_runtime_episode_event,
    enqueue_runtime_episode,
    normalize_capability_kind,
    upsert_runtime_episode,
)
from core.runtime_tool_access import (
    RUNTIME_BROKER_TOOL_NAME,
    grant_runtime_tool_groups,
    normalize_runtime_access,
    revoke_runtime_tool_groups,
    runtime_access_from_route_context,
    runtime_kind_available,
    runtime_tool_groups_catalog,
)
from core.runtime_route_contract import runtime_route_parameter_guidance
from core.runtime_continuation import (
    RuntimeContinuationContractError,
    normalize_runtime_continuation_request,
    validate_runtime_continuation_answers,
)
from core.spec_service import spec_service
from erc.runtime_context import get_runtime_context


class RuntimeRouteTaskBrief(BaseModel):
    """Supervisor-owned execution contract passed to a runtime episode."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    taskBriefId: str = Field(
        min_length=1,
        description="Stable Supervisor-owned task ID, unique within this route call.",
    )
    goal: str = Field(
        min_length=1,
        description=(
            "One short sentence naming this brief's outcome. Put detailed checklists in bounded context/refs; "
            "do not spend the argument budget on a long prose restatement."
        ),
    )
    context: dict[str, Any] | str = Field(
        default_factory=dict,
        description=(
            "Bounded current symptom plus high-value refs only; do not repeat a full research checklist or the user prompt."
        ),
    )
    writeRequired: bool = Field(
        default=False,
        description="True only when this brief must mutate the bound workspace.",
    )
    readOnly: bool = Field(
        default=False,
        description="True for evidence/review work that must not mutate the workspace.",
    )
    writeSet: list[str] = Field(
        default_factory=list,
        description=(
            "Exhaustive array of bounded paths relative to the original bound workspace that the task or its commands "
            "may create or modify, including temporary/cache/report files. Never copy an absolute managed-worktree "
            "path from a runtime handoff. Declare deterministic files, or contain variable filenames below one declared "
            "output directory. Use [] for read-only work; never pass a string."
        ),
    )
    expectedArtifacts: list[str] = Field(
        default_factory=list,
        description=(
            "Final deliverable paths only. Every final artifact must also be covered by writeSet; "
            "do not use this field as a broader write grant."
        ),
    )
    expectedOutputs: list[str] = Field(
        default_factory=list,
        description=(
            "Short array of the essential deliverable classes (normally 1-3), not one entry per sub-question."
        ),
    )
    acceptance: dict[str, Any] | list[Any] | str | None = Field(
        default=None,
        description="Legacy acceptance alias. New calls should use acceptanceContract.",
    )
    acceptanceContract: dict[str, Any] | list[Any] | str | None = Field(
        default=None,
        description="One to three concise checks that prove this brief is complete.",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Array of scope, safety, or implementation boundaries. Use [] or omit when empty.",
    )
    detailRefs: list[str] = Field(
        default_factory=list,
        description="Highest-value durable detail/spec/evidence references only (normally at most 3). Use [] or omit when empty.",
    )
    dependency: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dependencies", "dependency"),
        serialization_alias="dependencies",
        description=(
            "Canonical public field: dependencies. It is always an array of prerequisite taskBriefId values. "
            "Omit the field when there are no dependencies; never pass an empty string. "
            "The singular dependency spelling is a read-only legacy alias."
        ),
    )

    @field_validator("writeSet", "constraints", "detailRefs", "dependency", mode="before")
    @classmethod
    def _normalize_explicit_empty_list(cls, value: Any) -> Any:
        """Accept only a provider's common explicit-empty spelling for list fields.

        Some providers serialize an intended ``[]`` as ``""``.  Treating that
        as an empty list avoids a false-negative parameter failure while every
        non-empty string remains invalid.  The public schema and prompt still
        teach the canonical array type.
        """
        if isinstance(value, str) and not value.strip():
            return []
        return value

    @field_validator("expectedOutputs", "expectedArtifacts", mode="before")
    @classmethod
    def _normalize_explicit_expected_outputs(cls, value: Any) -> Any:
        """Canonicalize explicit provider output maps into the typed list."""
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, dict):
            return value
        normalized: list[str] = []
        for key, item in value.items():
            label = str(key or "").strip()
            if isinstance(item, list):
                for nested in item:
                    rendered = str(nested or "").strip()
                    if rendered:
                        normalized.append(f"{label}: {rendered}" if label else rendered)
                continue
            if isinstance(item, dict):
                rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
            else:
                rendered = str(item or "").strip()
            if rendered:
                normalized.append(f"{label}: {rendered}" if label else rendered)
            elif label:
                normalized.append(label)
        return normalized


class RuntimeRouteInputs(BaseModel):
    # extra="allow" intentionally keeps workerBriefs/tasks readable for old
    # persisted calls while the public schema advertises the two current
    # Canonical internal fields remain stable after the provider-facing
    # parallel Research arrays are restored at the tool boundary.
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    workspacePath: str | None = Field(
        default=None,
        description="Current bound workspace root. Do not borrow a workspace from another session.",
    )
    researchBriefs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Canonical Research transport: a complete map of stable taskBriefId to one short goal. "
            "Use this map for Research instead of a nested taskBriefs array; some OpenAI-compatible providers "
            "silently retain only the first object in nested arrays. Put every currently known independent fact "
            "domain in this one map before adding optional detail."
        ),
    )
    researchBriefContexts: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional compact Research context keyed by the same stable IDs as researchBriefs. "
            "Keys must be a subset of researchBriefs; values are bounded hints, not raw provider payloads."
        ),
    )
    taskBriefs: list[RuntimeRouteTaskBrief] = Field(
        default_factory=list,
        description=(
            "Canonical nested task array for Engineering, Creative Media, Computer Use, RPA, and Delegation routes. "
            "Research routes use the internal researchBriefs map restored from provider-safe parallel arrays. "
            "Engineering routes use one coherent independently executable/acceptable unit per brief and dependencies for ordering. "
            "When a request includes source implementation plus separately generated proof/report output, those MUST be separate briefs; "
            "the proof/report brief depends on the implementation brief."
        ),
    )
    proofExpectations: list[str] = Field(
        default_factory=list,
        description="Array of evidence the handoff must return, such as artifact refs and verification outcomes.",
    )
    @field_validator("proofExpectations", mode="before")
    @classmethod
    def _normalize_explicit_proof_expectations(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        return value

    @model_validator(mode="after")
    def _expand_research_brief_map(self) -> "RuntimeRouteInputs":
        brief_map = {
            str(brief_id or "").strip(): str(goal or "").strip()
            for brief_id, goal in dict(self.researchBriefs or {}).items()
            if str(brief_id or "").strip() and str(goal or "").strip()
        }
        context_map = {
            str(brief_id or "").strip(): str(context or "").strip()
            for brief_id, context in dict(self.researchBriefContexts or {}).items()
            if str(brief_id or "").strip() and str(context or "").strip()
        }
        if not brief_map:
            if context_map:
                raise ValueError("researchBriefContexts requires a non-empty researchBriefs map")
            return self
        unknown_context_ids = sorted(set(context_map) - set(brief_map))
        if unknown_context_ids:
            raise ValueError(
                "researchBriefContexts contains IDs absent from researchBriefs: "
                + ", ".join(unknown_context_ids[:8])
            )
        if self.taskBriefs:
            raise ValueError("Research routes must use either researchBriefs or taskBriefs, never both")
        self.researchBriefs = brief_map
        self.researchBriefContexts = context_map
        self.taskBriefs = [
            RuntimeRouteTaskBrief(
                taskBriefId=brief_id,
                goal=goal,
                context=context_map.get(brief_id, {}),
                readOnly=True,
                writeSet=[],
            )
            for brief_id, goal in brief_map.items()
        ]
        return self


class RuntimeRouteNeed(BaseModel):
    """Strong route envelope visible in the Supervisor tool schema."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    kind: Literal["research", "engineering", "creative_media", "computer_use", "rpa", "delegation"] = Field(
        description="Execution runtime family selected for this route."
    )
    source: str = Field(default="supervisor", description="Contract owner; normally supervisor.")
    reason: str = Field(
        min_length=1,
        description="Why this runtime is the correct execution path for the current user task.",
    )
    inputs: RuntimeRouteInputs = Field(
        default_factory=RuntimeRouteInputs,
        description=(
            "Typed workspace, complete Research brief map or specialist taskBriefs, and proof expectations. "
            "For Research use inputs.researchBriefs; other routes use inputs.taskBriefs."
        ),
    )

    @model_validator(mode="after")
    def _validate_runtime_transport(self) -> "RuntimeRouteNeed":
        transport_conflicts = list((self.model_extra or {}).get("transportConflicts") or [])
        transport_errors = list((self.model_extra or {}).get("transportErrors") or [])
        if transport_conflicts:
            raise ValueError(
                "Conflicting flat and legacy route fields: " + ", ".join(str(item) for item in transport_conflicts[:8])
            )
        if transport_errors:
            raise ValueError("Invalid provider route transport: " + ", ".join(str(item) for item in transport_errors[:8]))
        if self.kind != "research" and self.inputs.researchBriefs:
            raise ValueError("researchBriefs is only valid for Research routes")
        legacy_inputs = dict(self.inputs.model_extra or {})
        has_legacy_briefs = any(
            isinstance(legacy_inputs.get(key), list) and legacy_inputs.get(key)
            for key in ("workerBriefs", "worker_briefs", "tasks")
        )
        if self.kind == "research" and not self.inputs.taskBriefs and not has_legacy_briefs:
            raise ValueError("Research route requires at least one complete Research brief")
        return self


class RuntimeRoutePublicNeed(BaseModel):
    """Deprecated read-compatible route envelope hidden from provider schemas."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    kind: Literal["research", "engineering", "creative_media", "computer_use", "rpa", "delegation"] = Field(
        description="Execution runtime family selected for this route."
    )
    source: str = Field(default="supervisor", description="Contract owner; normally supervisor.")
    reason: str = Field(
        min_length=1,
        description="Why this runtime is the correct execution path for the current user task.",
    )
    workspacePath: str | None = Field(
        default=None,
        description="Current bound workspace root; omit when the session binding already supplies it.",
    )
    researchBriefs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Research only: complete stable taskBriefId -> one-sentence goal map. "
            "Put every currently known independent fact domain in this map."
        ),
    )
    researchBriefContexts: dict[str, str] = Field(
        default_factory=dict,
        description="Research only: optional compact context keyed by IDs present in researchBriefs.",
    )
    taskBriefs: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Non-Research execution briefs. Each object requires taskBriefId and goal. A write brief also requires "
            "writeRequired=true, bounded writeSet, expectedOutputs, and acceptanceContract; expectedArtifacts must "
            "be covered by writeSet. Use dependencies for ordering. Engine performs the strict field/type validation."
        ),
    )
    proofExpectations: list[str] = Field(
        default_factory=list,
        description="Compact evidence outcomes the terminal handoff must return.",
    )


class RuntimeBrokerArgs(BaseModel):
    """Strict public arguments for the Supervisor runtime entry."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mode: str = Field(
        default="list",
        description="Operation. Use route for execution, list for the compact catalog, and grant/revoke only for explicit run-scoped tool groups.",
    )
    runtime_kind: str | None = Field(
        default=None,
        description="Legacy/list/grant hint. For mode=route use routeKind.",
    )
    tool_group: str | None = Field(
        default=None,
        description="Single run-scoped tool group for mode=grant/revoke.",
    )
    tool_groups: list[str] | None = Field(
        default=None,
        description="Array of run-scoped tool groups for mode=grant/revoke.",
    )
    reason: str | None = Field(
        default=None,
        description="Grant/revoke audit reason. For mode=route use routeReason.",
    )
    detail_level: str = Field(
        default="summary",
        description="summary by default; catalog/detail/full only when diagnostics are needed.",
    )
    routeKind: Literal["research", "engineering", "creative_media", "computer_use", "rpa", "delegation"] | None = Field(
        default=None,
        description="For mode=route: the specialist runtime family.",
    )
    routeReason: str | None = Field(
        default=None,
        description="For mode=route: one short sentence explaining why this runtime is the correct path.",
    )
    workspacePath: str | None = Field(
        default=None,
        description="For mode=route: current bound workspace root; omit when session binding already supplies it.",
    )
    researchBriefIds: list[str] = Field(
        default_factory=list,
        description=(
            "Research route only: complete ordered list of every currently known stable taskBriefId. "
            "List all IDs before optional detail."
        ),
    )
    researchBriefGoals: list[str] = Field(
        default_factory=list,
        description=(
            "Research route only: short goals matching researchBriefIds by position. "
            "The two arrays must have equal length; never omit an already-known domain."
        ),
    )
    researchBriefContexts: list[str] = Field(
        default_factory=list,
        description=(
            "Research route only: optional compact contexts matching researchBriefIds by position. "
            "Omit the whole array when no context is needed."
        ),
    )
    taskBriefs: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Non-Research execution briefs, used only for non-Research routes. Each object requires taskBriefId and goal. "
            "A write brief also requires writeRequired=true, bounded writeSet, expectedOutputs, and acceptanceContract; "
            "expectedArtifacts must be covered by writeSet. Every writeSet entry is relative to the original bound "
            "workspace, never an absolute managed-worktree path copied from a handoff. Engine performs the strict "
            "field/type validation."
        ),
    )
    proofExpectations: list[str] = Field(
        default_factory=list,
        description="For mode=route: compact evidence outcomes the terminal handoff must return.",
    )
    need: SkipJsonSchema[RuntimeRoutePublicNeed | None] = Field(
        default=None,
        description="Deprecated read-compatible route envelope; hidden from provider schemas.",
    )
    allow_direct_fallback: bool = Field(
        default=False,
        description="Internal compatibility flag. Keep false for ordinary Supervisor routing.",
    )
    episode_id: str | None = Field(
        default=None,
        description="Required only for mode=resume. Must identify the waiting_input episode in the current session.",
    )
    continuation_request_id: str | None = Field(
        default=None,
        description="Required only for mode=resume. Must exactly match the latest waiting continuationRequest.requestId.",
    )
    continuation_inputs: dict[str, Any] | None = Field(
        default=None,
        description="Required only for mode=resume. Exact user/Supervisor answers keyed by requiredInputs.id.",
    )
    tool_call_id: Annotated[str, InjectedToolCallId] = ""
    state: Annotated[dict[str, Any], InjectedState] = None


def _runtime_broker_validation_error(error: ValidationError) -> str:
    unknown_fields = [
        str(item.get("loc", [""])[-1])
        for item in error.errors(include_url=False, include_context=False, include_input=False)
        if str(item.get("type") or "") == "extra_forbidden" and item.get("loc")
    ]
    return json.dumps(
        {
            "ok": False,
            "error": "typed_tool_arguments_invalid",
            "summary": "runtime_broker rejected arguments outside its typed contract.",
            "unknownFields": list(dict.fromkeys(unknown_fields))[:8],
            "nextAction": (
                "Retry once with every Research domain in the matching researchBriefIds/researchBriefGoals arrays "
                "or every other runtime work unit in taskBriefs. "
                "Do not place a brief in top-level item/task/brief fields."
            ),
        },
        ensure_ascii=False,
    )


def _model_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    return value


_PUBLIC_ROUTE_INPUT_FIELDS = (
    "workspacePath",
    "researchBriefs",
    "researchBriefContexts",
    "taskBriefs",
    "proofExpectations",
)


def _restore_internal_route_need(value: Any) -> Any:
    """Translate the shallow provider transport into the canonical inputs envelope."""

    if not isinstance(value, dict):
        return value
    payload = dict(value)
    nested_inputs = dict(payload.pop("inputs", {}) or {}) if isinstance(payload.get("inputs"), dict) else {}
    conflicts: list[str] = []
    for field_name in _PUBLIC_ROUTE_INPUT_FIELDS:
        if field_name not in payload:
            continue
        flat_value = payload.pop(field_name)
        nested_value = nested_inputs.get(field_name)
        flat_meaningful = flat_value not in (None, "", [], {})
        nested_meaningful = nested_value not in (None, "", [], {})
        if flat_meaningful and nested_meaningful and flat_value != nested_value:
            conflicts.append(field_name)
            continue
        if flat_meaningful or not nested_meaningful:
            nested_inputs[field_name] = flat_value
    payload["inputs"] = nested_inputs
    if conflicts:
        payload["transportConflicts"] = conflicts
    return payload


def _route_need_from_public_transport(
    value: Any,
    *,
    route_kind: Any = None,
    route_reason: Any = None,
    workspace_path: Any = None,
    research_brief_ids: Any = None,
    research_brief_goals: Any = None,
    research_brief_contexts: Any = None,
    task_briefs: Any = None,
    proof_expectations: Any = None,
) -> Any:
    """Restore the provider-safe root transport to the canonical route need."""

    legacy = _model_payload(value)
    root_values = [
        route_kind,
        route_reason,
        workspace_path,
        research_brief_ids,
        research_brief_goals,
        research_brief_contexts,
        task_briefs,
        proof_expectations,
    ]
    has_root_transport = any(item not in (None, "", [], {}) for item in root_values)
    if isinstance(legacy, dict):
        restored = _restore_internal_route_need(legacy)
        if has_root_transport and isinstance(restored, dict):
            restored["transportConflicts"] = ["legacy need and root route fields"]
        return restored
    if not has_root_transport:
        return None

    brief_ids = [str(item or "").strip() for item in list(research_brief_ids or [])]
    brief_goals = [str(item or "").strip() for item in list(research_brief_goals or [])]
    brief_contexts = [str(item or "").strip() for item in list(research_brief_contexts or [])]
    transport_errors: list[str] = []
    if len(brief_ids) != len(brief_goals):
        transport_errors.append("researchBriefIds and researchBriefGoals must have equal length")
    if brief_contexts and len(brief_contexts) != len(brief_ids):
        transport_errors.append("researchBriefContexts must be omitted or match researchBriefIds length")
    if any(not item for item in brief_ids):
        transport_errors.append("researchBriefIds cannot contain blank IDs")
    if any(not item for item in brief_goals):
        transport_errors.append("researchBriefGoals cannot contain blank goals")
    if len(set(brief_ids)) != len(brief_ids):
        transport_errors.append("researchBriefIds must be unique")

    inputs: dict[str, Any] = {}
    if str(workspace_path or "").strip():
        inputs["workspacePath"] = str(workspace_path).strip()
    if brief_ids and len(brief_ids) == len(brief_goals) and not transport_errors:
        inputs["researchBriefs"] = dict(zip(brief_ids, brief_goals))
        if brief_contexts:
            inputs["researchBriefContexts"] = {
                brief_id: context
                for brief_id, context in zip(brief_ids, brief_contexts)
                if context
            }
    if list(task_briefs or []):
        inputs["taskBriefs"] = list(task_briefs or [])
    if list(proof_expectations or []):
        inputs["proofExpectations"] = list(proof_expectations or [])
    payload: dict[str, Any] = {
        "kind": route_kind,
        "reason": route_reason,
        "inputs": inputs,
    }
    if transport_errors:
        payload["transportErrors"] = transport_errors
    return payload


def _public_route_validation_field(location: Any) -> str:
    """Project internal validation paths back onto the provider-visible schema."""

    parts = [str(part) for part in list(location or [])]
    if parts and parts[0] == "inputs":
        parts = parts[1:]
    if parts and parts[0] == "kind":
        parts[0] = "routeKind"
    elif parts and parts[0] == "reason":
        parts[0] = "routeReason"
    elif parts and parts[0] in {"researchBriefs", "researchBriefContexts"}:
        return "researchBriefIds/researchBriefGoals"
    return ".".join(parts) if parts else "route"


def _runtime_broker_payload(
    *,
    mode: str,
    ok: bool,
    summary: str,
    grants: list[dict[str, Any]] | None = None,
    groups: list[dict[str, Any]] | None = None,
    rejected: list[str] | None = None,
    error: str | None = None,
    detail_level: str = "summary",
    changed: list[dict[str, Any]] | None = None,
    episode: dict[str, Any] | None = None,
    next_action: str | None = None,
    route_brief_quality: dict[str, Any] | None = None,
    detail_ref: str | None = None,
    parameter_guidance: dict[str, Any] | None = None,
) -> str:
    normalized_detail = str(detail_level or "summary").strip().lower()
    group_items = list(groups or [])
    if normalized_detail not in {"catalog", "detail", "full"}:
        original_group_count = len(group_items)
        group_items = [
            {
                "group": str(item.get("group") or ""),
                "kind": str(item.get("runtimeKind") or ""),
                "label": str(item.get("label") or item.get("group") or ""),
            }
            for item in group_items
            if isinstance(item, dict)
        ][:6]
    else:
        original_group_count = len(group_items)
    payload = {
        "mode": mode,
        "ok": ok,
        "summary": summary,
        "activeGrants": [str((item or {}).get("group") or item) for item in list(grants or [])],
        "availableGroups": group_items,
        "rejected": list(rejected or []),
        "detailMode": normalized_detail if normalized_detail in {"catalog", "detail", "full"} else "summary",
        "detailTool": "runtime_broker(mode='list', detail_level='catalog') for compact catalog; detail_level='full' for diagnostics",
    }
    if changed is not None:
        payload["changed"] = list(changed or [])
    if episode:
        episode_id = str(episode.get("episodeId") or episode.get("needId") or "")
        episode_kind = str(episode.get("kind") or "")
        episode_state = str(episode.get("state") or "")
        payload["episode"] = {
            "episodeId": episode_id,
            "kind": episode_kind,
            "state": episode_state,
            "reason": str(episode.get("reason") or ""),
            "continuationTarget": str(episode.get("continuationTarget") or ""),
        }
        payload["queuedEpisodeId"] = episode_id
        payload["episodeKind"] = episode_kind
        payload["state"] = episode_state
        payload["nextAction"] = "runtime_episode"
    if next_action:
        payload["recommendedNextAction"] = next_action
    if detail_ref:
        payload["detailRef"] = detail_ref
    if route_brief_quality:
        payload["routeBriefQuality"] = dict(route_brief_quality)
    if parameter_guidance:
        payload["parameterGuidance"] = dict(parameter_guidance)
    if normalized_detail not in {"catalog", "detail", "full"} and groups:
        omitted_tools = sum(len(list(item.get("toolNames") or [])) for item in list(groups or []) if isinstance(item, dict))
        payload["omitted"] = {
            "toolNames": omitted_tools,
            "availableGroups": max(0, original_group_count - len(group_items)),
            "reason": "default list is a compact route menu; capability_registry already describes runtime details",
        }
    if error:
        payload["error"] = error
    if normalized_detail in {"catalog", "detail", "full"}:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_RUNTIME_ROUTE_DEFAULT_GROUPS: dict[str, list[str]] = {
    "engineering": ["delegation.recursive"],
    "research": ["research.core"],
    "creative_media": [],
    "computer_use": ["computer_use.control"],
    "rpa": ["rpa.run"],
    "delegation": ["delegation.recursive"],
    "memory": ["memory.read"],
}


def _normalize_capability_kind(value: Any) -> str:
    return normalize_capability_kind(value)


_READY_HANDOFF_STATES = {"ok", "ready", "success", "completed", "done"}


def _handoff_runtime_kind(handoff: dict[str, Any]) -> str:
    value = str(handoff.get("kind") or handoff.get("runtimeKind") or "").strip().lower()
    for kind in ("engineering", "research", "creative_media", "computer_use", "rpa", "delegation"):
        if kind in value:
            return kind
    if "subagent" in value:
        return "delegation"
    return ""


def _walk_handoff_nodes(value: Any, *, depth: int = 0):
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for key in ("delegationHandoff", "childHandoffs", "results"):
            nested = value.get(key)
            if isinstance(nested, dict):
                yield from _walk_handoff_nodes(nested, depth=depth + 1)
            elif isinstance(nested, list):
                for item in nested:
                    yield from _walk_handoff_nodes(item, depth=depth + 1)


def _flatten_reference_values(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            references.add(normalized)
        return references
    if isinstance(value, list):
        for item in value:
            references.update(_flatten_reference_values(item))
        return references
    if not isinstance(value, dict):
        return references
    for key in (
        "ref",
        "id",
        "handoffRefId",
        "handoffId",
        "artifactId",
        "producerEpisodeId",
        "commitId",
        "rawRef",
        "detailRef",
    ):
        references.update(_flatten_reference_values(value.get(key)))
    for key in ("refs", "artifactRefs", "proofRefs", "verificationRefs", "detailRefs"):
        references.update(_flatten_reference_values(value.get(key)))
    return references


def _handoff_reference_values(handoff: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for node in _walk_handoff_nodes(handoff):
        references.update(_flatten_reference_values(node))
    return references


def _handoff_has_complete_evidence(handoff: dict[str, Any]) -> bool:
    for node in _walk_handoff_nodes(handoff):
        status = str(node.get("status") or "").strip().lower()
        if status not in _READY_HANDOFF_STATES:
            continue
        if node.get("error") or node.get("missingArtifactEvidence"):
            continue
        if list(node.get("blockers") or node.get("residualRisks") or []):
            continue
        verification_evidence = (
            dict(node.get("verificationEvidence"))
            if isinstance(node.get("verificationEvidence"), dict)
            else {}
        )
        verification_passed = verification_evidence.get("passed") is True
        if not verification_passed:
            verification_passed = any(
                isinstance(result, dict)
                and (
                    result.get("passed") is True
                    or str(result.get("status") or "").strip().lower()
                    in {"verified", "passed", "success", "completed"}
                )
                for result in list(node.get("verificationResults") or [])
            )
        if verification_passed or _flatten_reference_values(
            [node.get("artifactRefs"), node.get("proofRefs"), node.get("verificationRefs")]
        ):
            return True
    return False


def _route_prior_reference_values(need: dict[str, Any]) -> set[str]:
    inputs = dict(need.get("inputs") or {}) if isinstance(need.get("inputs"), dict) else {}
    references: set[str] = set()
    sources = [inputs]
    sources.extend(
        item for item in list(inputs.get("taskBriefs") or []) if isinstance(item, dict)
    )
    for source in sources:
        context = source.get("context") if isinstance(source.get("context"), dict) else {}
        for value in (source, context):
            for key in ("priorRefs", "evidenceRefs", "artifactRefs", "proofRefs", "detailRefs"):
                references.update(_flatten_reference_values(value.get(key)))
    return references


def _has_user_instruction_after_runtime_handoff(state: dict[str, Any]) -> bool:
    for message in reversed(list(state.get("messages") or [])):
        if not isinstance(message, HumanMessage):
            continue
        metadata = dict(getattr(message, "additional_kwargs", None) or {})
        governance_type = str(metadata.get("v8_governance_type") or "").strip()
        if governance_type == "runtime_handoff":
            break
        if not governance_type and str(getattr(message, "content", "") or "").strip():
            return True
    return False


def _current_governed_handoff_reuse(
    *,
    state: dict[str, Any] | None,
    need: dict[str, Any],
    route_kind: str,
) -> dict[str, Any] | None:
    """Reuse same-run proof when a resumed Supervisor re-routes its own read-only check.

    This is an idempotency boundary, not a permission boundary. A later user
    instruction, a write task, missing proof, or contradictory handoff still
    creates a new episode normally.
    """

    state = dict(state or {})
    dispatch_status = dict(state.get("runtime_dispatch_status") or {})
    if (
        str(dispatch_status.get("mode") or "").strip() != "runtime_episode"
        or str(dispatch_status.get("nextAction") or "").strip() != "resume_supervisor"
        or str(dispatch_status.get("state") or "").strip() != "handoff_ready"
        or _has_user_instruction_after_runtime_handoff(state)
    ):
        return None
    inputs = dict(need.get("inputs") or {}) if isinstance(need.get("inputs"), dict) else {}
    task_briefs = [item for item in list(inputs.get("taskBriefs") or []) if isinstance(item, dict)]
    if not task_briefs or any(
        not bool(item.get("readOnly"))
        or bool(item.get("writeRequired"))
        or bool(list(item.get("writeSet") or []))
        for item in task_briefs
    ):
        return None
    requested_refs = _route_prior_reference_values(need)
    if not requested_refs:
        return None
    route_context = dict(state.get("current_route_context") or {})
    for handoff in reversed(list(route_context.get("handoffRefs") or [])):
        if not isinstance(handoff, dict):
            continue
        if _handoff_runtime_kind(handoff) != route_kind:
            continue
        if bool(handoff.get("requiresContinuation")):
            continue
        if str(handoff.get("status") or "").strip().lower() not in _READY_HANDOFF_STATES:
            continue
        handoff_refs = _handoff_reference_values(handoff)
        matching_refs = sorted(requested_refs & handoff_refs)
        if not matching_refs or not _handoff_has_complete_evidence(handoff):
            continue
        return {
            "handoffRefId": str(handoff.get("handoffRefId") or handoff.get("handoffId") or "").strip(),
            "producerEpisodeId": str(handoff.get("producerEpisodeId") or "").strip(),
            "matchingRefs": matching_refs[:8],
        }
    return None


def _capability_route_groups(
    *,
    need: dict[str, Any],
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    tool_groups: Optional[list[str]],
) -> list[str]:
    kind = _normalize_capability_kind(need.get("kind") or runtime_kind)
    if kind == "creative_media":
        return []
    requested: list[str] = []
    requested.extend(list(need.get("requiredRuntimeAccess") or []))
    requested.extend(list(tool_groups or []))
    if tool_group:
        requested.append(tool_group)
    requested.extend(_RUNTIME_ROUTE_DEFAULT_GROUPS.get(kind, []))
    return normalize_runtime_access(requested, runtime_kind=runtime_kind or kind)


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


def _explicit_task_briefs_from_inputs(inputs: dict[str, Any] | None) -> list[dict[str, Any]]:
    inputs = dict(inputs or {})
    return normalize_task_briefs(inputs.get("workerBriefs") or inputs.get("taskBriefs") or inputs.get("tasks") or [])


def _route_brief_is_write_task(brief: dict[str, Any], *, kind: str) -> bool:
    if bool(brief.get("readOnly") or brief.get("read_only")):
        return False
    if bool(brief.get("writeRequired") or brief.get("write_required")):
        return True
    if list(brief.get("writeSet") or brief.get("write_set") or []):
        return True
    if kind == "engineering":
        return True
    family = str(brief.get("familyHint") or brief.get("family_hint") or "").strip().lower()
    capabilities = " ".join(str(item or "") for item in list(brief.get("requiredCapabilities") or [])).lower()
    deliverable_kind = str(brief.get("deliverableKind") or brief.get("deliverable_kind") or "").strip().lower()
    return bool(
        family == "engineering"
        or any(marker in capabilities for marker in ("workspace_mutation", "file_write", "implementation"))
        or deliverable_kind in {"file", "files", "code", "project", "artifact"}
    )


_URLISH_HOST_PATH = re.compile(
    r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*"
    r"\.(?:ai|app|cloud|cn|co|com|dev|edu|gov|io|net|org)(?:/|$)",
    re.IGNORECASE,
)


def _declared_artifact_paths_outside_write_set(
    *,
    write_set: list[str],
    workspace_path: str = "",
    explicit_artifacts: list[str] | None = None,
) -> list[str]:
    """Return explicit expected artifact paths not covered by ``writeSet``.

    Acceptance is intentionally excluded.  It is human-readable proof prose,
    not an authority source: backticked sentences, URLs, slash-separated
    technology names, inputs, and forbidden paths must never be guessed into a
    write contract.  Only the typed ``expectedArtifacts`` field may contradict
    the explicit grant, and this check never widens that grant.
    """

    def _normal(value: Any) -> str:
        text = str(value or "").strip().strip("`'\"").replace("\\", "/")
        text = re.sub(r"^\./+", "", text)
        text = re.sub(r"/+", "/", text)
        return text.rstrip("/.,;:)]}>").lower()

    workspace = _normal(workspace_path)
    declared_entries = [
        (_normal(item), str(item or "").strip().replace("\\", "/").endswith("/"))
        for item in list(write_set or [])
        if _normal(item)
    ]
    declared = [item for item, _is_directory in declared_entries]

    def _is_absolute(value: str) -> bool:
        return value.startswith("/") or bool(re.match(r"^[a-z]:/", value, re.IGNORECASE))

    def _covered(candidate: str) -> bool:
        token = _normal(candidate)
        if (
            not token
            or token.startswith(("http://", "https://", "file://", "spec://"))
            or _URLISH_HOST_PATH.match(token)
        ):
            return True
        token_variants = {token}
        if workspace and not _is_absolute(token):
            token_variants.add(_normal(f"{workspace}/{token}"))
        for grant in declared:
            grant_variants = {grant}
            if workspace and not _is_absolute(grant):
                grant_variants.add(_normal(f"{workspace}/{grant}"))
            for candidate_variant in token_variants:
                for grant_variant in grant_variants:
                    if (
                        candidate_variant == grant_variant
                        or candidate_variant.startswith(grant_variant.rstrip("/") + "/")
                        # expectedArtifacts may use a workspace-relative suffix
                        # after writeSet names the same file below a task
                        # directory. Segment-safe suffix matching does not widen
                        # write authority.
                        or grant_variant.endswith("/" + candidate_variant)
                    ):
                        return True
        # A Spec may name a declared output directory by its full path, then
        # name one expected child relative to that directory's parent. This is
        # still the same explicit directory grant.
        for grant, is_directory in declared_entries:
            if not is_directory or "/" not in grant:
                continue
            grant_name = grant.rsplit("/", 1)[-1]
            if token == grant_name or token.startswith(grant_name + "/"):
                return True
        return False

    found: list[str] = []
    for token in list(explicit_artifacts or []):
        normalized = _normal(token)
        if (
            normalized
            and ("/" in normalized or "." in normalized)
            and not _covered(normalized)
            and normalized not in found
        ):
            found.append(normalized)
    return found[:24]


def _route_task_contract_quality(tasks: list[dict[str, Any]], *, kind: str, workspace_path: str = "") -> dict[str, Any]:
    if not tasks:
        return {
            "status": "blocked",
            "reason": "task_brief_required",
            "blocking": True,
            "message": "runtime_broker(route) requires an explicit Supervisor-owned task contract.",
            "requiredFields": ["taskBriefId", "goal", "context"],
        }

    failures: list[dict[str, Any]] = []
    normalized_workspace = str(Path(workspace_path).resolve()).lower() if workspace_path else ""
    for index, brief in enumerate(tasks):
        task_id = str(brief.get("taskBriefId") or brief.get("id") or f"task-{index + 1}").strip()
        missing: list[str] = []
        if not str(brief.get("goal") or "").strip():
            missing.append("goal")
        if _route_brief_is_write_task(brief, kind=kind):
            write_set = [str(item or "").strip() for item in list(brief.get("writeSet") or []) if str(item or "").strip()]
            expected_outputs = [str(item or "").strip() for item in list(brief.get("expectedOutputs") or []) if str(item or "").strip()]
            acceptance = brief.get("acceptanceContract") or brief.get("acceptance")
            acceptance_tiers = brief.get("acceptanceTiers") if isinstance(brief.get("acceptanceTiers"), dict) else {}
            has_acceptance = bool(
                (isinstance(acceptance, dict) and acceptance)
                or (isinstance(acceptance, list) and acceptance)
                or str(acceptance or "").strip()
                or any(list(acceptance_tiers.get(key) or []) for key in ("must", "should", "nice"))
            )
            if not write_set:
                missing.append("writeSet")
            elif normalized_workspace:
                resolved_write_set = []
                workspace_root = Path(workspace_path).resolve()
                for item in write_set:
                    try:
                        candidate = Path(item)
                        if not candidate.is_absolute():
                            candidate = workspace_root / candidate
                        resolved_write_set.append(str(candidate.resolve()).lower())
                    except Exception:
                        continue
                if resolved_write_set and all(item == normalized_workspace for item in resolved_write_set):
                    missing.append("writeSet(file_or_directory_below_workspace)")
            if not expected_outputs:
                missing.append("expectedOutputs")
            if not has_acceptance:
                missing.append("acceptance")
            undeclared_output_paths = _declared_artifact_paths_outside_write_set(
                write_set=write_set,
                workspace_path=workspace_path,
                explicit_artifacts=list(brief.get("expectedArtifacts") or []),
            )
            if undeclared_output_paths:
                missing.append("writeSet(expected_artifact_not_declared)")
            failure = {"taskBriefId": task_id, "missingFields": missing}
            if undeclared_output_paths:
                failure["undeclaredArtifactPaths"] = undeclared_output_paths
            if missing:
                failures.append(failure)
                continue
        if missing:
            failures.append({"taskBriefId": task_id, "missingFields": missing})

    if failures:
        return {
            "status": "blocked",
            "reason": "write_task_contract_incomplete",
            "blocking": True,
            "message": (
                "Write-capable runtime tasks require an exhaustive writeSet, expectedOutputs, and acceptance contract. "
                "Every explicit expectedArtifacts path must be covered by writeSet; acceptance prose, the workspace root, "
                "dependencies, and input files are never write grants."
            ),
            "tasks": failures,
            "requiredFields": ["writeSet", "expectedOutputs", "acceptance"],
        }
    return {"status": "ready", "reason": "explicit_task_contract", "blocking": False}


_ENGINEERING_REPAIR_FAILURE_STATES = {"degraded", "failed"}
_ENGINEERING_REPAIR_BUDGET = 1
_RESEARCH_REPAIR_BUDGET = 1
_RESEARCH_TERMINAL_STATES = {"completed", "degraded", "failed", "cancelled"}
_RESEARCH_ACTIVE_STATES = {"queued", "leased", "running", "waiting_external_tool", "waiting_input"}
_RESEARCH_NON_CONSUMING_FAILURE_CODES = {"episode_runner_unavailable"}


def _normalized_engineering_write_scope(tasks: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return the exact union of explicitly granted Engineering paths."""

    normalized: set[str] = set()
    for brief in tasks:
        if not isinstance(brief, dict):
            continue
        for item in list(brief.get("writeSet") or brief.get("write_set") or []):
            path = str(item or "").strip().replace("\\", "/")
            path = re.sub(r"^\./+", "", path)
            path = re.sub(r"/+", "/", path).rstrip("/")
            if not path:
                continue
            if os.name == "nt" or re.match(r"^[A-Za-z]:/", path):
                path = path.casefold()
            normalized.add(path)
    return tuple(sorted(normalized))


def _engineering_route_retry_state(
    *,
    tasks: list[dict[str, Any]],
    route_context: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Enforce one durable repair for the same Engineering write scope.

    Task IDs and brief grouping are not identity: a model must not bypass the
    budget by renaming or recombining tasks that grant the same union of paths.
    Persisted run episodes supplement the checkpoint projection so a restart
    cannot silently reset the budget.
    """

    write_scope = _normalized_engineering_write_scope(tasks)
    if not write_scope:
        return {
            "priorFailedAttempts": 0,
            "repairBudget": _ENGINEERING_REPAIR_BUDGET,
            "repairAttempt": 0,
            "exhausted": False,
        }

    context = dict(route_context or {})
    episode_by_id: dict[str, dict[str, Any]] = {}
    anonymous_episodes: list[dict[str, Any]] = []

    def _remember_episode(raw_episode: Any) -> None:
        if not isinstance(raw_episode, dict):
            return
        episode = dict(raw_episode)
        episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
        if episode_id:
            episode_by_id[episode_id] = {**episode_by_id.get(episode_id, {}), **episode}
        else:
            anonymous_episodes.append(episode)

    for raw_episode in list(context.get("capabilityEpisodes") or []):
        _remember_episode(raw_episode)

    runtime_context = get_runtime_context()
    state_payload = dict(state or {})
    run_id = str(
        runtime_context.get("run_id")
        or runtime_context.get("runId")
        or state_payload.get("run_id")
        or state_payload.get("runId")
        or context.get("run_id")
        or context.get("runId")
        or ""
    ).strip()
    if run_id:
        try:
            for raw_episode in db.list_runtime_episodes(run_id=run_id, limit=100):
                _remember_episode(raw_episode)
        except Exception:
            # Checkpoint truth remains sufficient for the current graph turn;
            # database recovery is an additional durability rail.
            pass

    prior_failed_attempts = 0
    for episode in [*episode_by_id.values(), *anonymous_episodes]:
        if str(episode.get("kind") or "").strip().lower() != "engineering":
            continue
        if str(episode.get("state") or "").strip().lower() not in _ENGINEERING_REPAIR_FAILURE_STATES:
            continue
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), dict) else {}
        if not inputs and isinstance(episode.get("need"), dict):
            candidate_inputs = episode["need"].get("inputs")
            inputs = candidate_inputs if isinstance(candidate_inputs, dict) else {}
        episode_tasks = _explicit_task_briefs_from_inputs(inputs)
        if _normalized_engineering_write_scope(episode_tasks) == write_scope:
            prior_failed_attempts += 1

    return {
        "priorFailedAttempts": prior_failed_attempts,
        "repairBudget": _ENGINEERING_REPAIR_BUDGET,
        "repairAttempt": min(prior_failed_attempts, _ENGINEERING_REPAIR_BUDGET),
        "exhausted": prior_failed_attempts > _ENGINEERING_REPAIR_BUDGET,
    }


def _normalized_research_brief_ids(tasks: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return the stable identities of one managed Research branch."""

    return tuple(
        sorted(
            {
                str(task.get("taskBriefId") or task.get("task_brief_id") or "").strip()
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("taskBriefId") or task.get("task_brief_id") or "").strip()
            }
        )
    )


def _same_research_branch(
    requested_brief_ids: tuple[str, ...],
    episode_brief_ids: tuple[str, ...],
) -> bool:
    """Treat a missing-evidence subset as repair work for its parent branch.

    A Research handoff commonly asks the Supervisor to retry only the missing
    briefs. Exact tuple equality therefore lets the model reset the durable
    budget simply by narrowing the contract. Disjoint contracts remain
    independent; partial overlaps that introduce new briefs are not guessed to
    be the same branch.
    """

    requested = set(requested_brief_ids)
    existing = set(episode_brief_ids)
    if not requested or not existing:
        return False
    return requested.issubset(existing) or existing.issubset(requested)


def _research_episode_consumed_attempt(episode: dict[str, Any]) -> bool:
    """Return whether a terminal episode actually consumed Research work."""

    error_code = str(episode.get("errorCode") or episode.get("error_code") or "").strip().lower()
    if error_code in _RESEARCH_NON_CONSUMING_FAILURE_CODES:
        return False
    return str(episode.get("state") or "").strip().lower() in _RESEARCH_TERMINAL_STATES


def _research_route_retry_state(
    *,
    tasks: list[dict[str, Any]],
    route_context: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bound one Research branch to an initial attempt and one durable repair.

    Task brief IDs are the Supervisor-authored contract identities. Both the
    checkpoint projection and persisted episodes participate so an Engine
    restart cannot reset the budget or create a duplicate in-flight branch.
    """

    brief_ids = _normalized_research_brief_ids(tasks)
    if not brief_ids:
        return {
            "taskBriefIds": [],
            "priorAttempts": 0,
            "repairBudget": _RESEARCH_REPAIR_BUDGET,
            "repairAttempt": 0,
            "exhausted": False,
            "inFlightEpisodeIds": [],
        }

    context = dict(route_context or {})
    episode_by_id: dict[str, dict[str, Any]] = {}
    anonymous_episodes: list[dict[str, Any]] = []

    def _remember_episode(raw_episode: Any) -> None:
        if not isinstance(raw_episode, dict):
            return
        episode = dict(raw_episode)
        episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
        if episode_id:
            episode_by_id[episode_id] = {**episode_by_id.get(episode_id, {}), **episode}
        else:
            anonymous_episodes.append(episode)

    for raw_episode in list(context.get("capabilityEpisodes") or []):
        _remember_episode(raw_episode)

    runtime_context = get_runtime_context()
    state_payload = dict(state or {})
    run_id = str(
        runtime_context.get("run_id")
        or runtime_context.get("runId")
        or state_payload.get("run_id")
        or state_payload.get("runId")
        or context.get("run_id")
        or context.get("runId")
        or ""
    ).strip()
    if run_id:
        try:
            for raw_episode in db.list_runtime_episodes(run_id=run_id, limit=100):
                _remember_episode(raw_episode)
        except Exception:
            pass

    prior_attempts = 0
    in_flight_episode_ids: list[str] = []
    for episode in [*episode_by_id.values(), *anonymous_episodes]:
        if str(episode.get("kind") or "").strip().lower() != "research":
            continue
        inputs = episode.get("inputs") if isinstance(episode.get("inputs"), dict) else {}
        if not inputs and isinstance(episode.get("need"), dict):
            candidate_inputs = episode["need"].get("inputs")
            inputs = candidate_inputs if isinstance(candidate_inputs, dict) else {}
        episode_brief_ids = _normalized_research_brief_ids(_explicit_task_briefs_from_inputs(inputs))
        if not _same_research_branch(brief_ids, episode_brief_ids):
            continue
        episode_state = str(episode.get("state") or "").strip().lower()
        if _research_episode_consumed_attempt(episode):
            prior_attempts += 1
        elif episode_state in _RESEARCH_ACTIVE_STATES:
            episode_id = str(episode.get("episodeId") or episode.get("id") or episode.get("needId") or "").strip()
            if episode_id:
                in_flight_episode_ids.append(episode_id)

    return {
        "taskBriefIds": list(brief_ids),
        "priorAttempts": prior_attempts,
        "repairBudget": _RESEARCH_REPAIR_BUDGET,
        "repairAttempt": min(prior_attempts, _RESEARCH_REPAIR_BUDGET),
        "exhausted": prior_attempts > _RESEARCH_REPAIR_BUDGET,
        "inFlightEpisodeIds": sorted(set(in_flight_episode_ids)),
    }


def _safe_compact_text(value: Any, *, limit: int = 6000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _spec_id_from_route_need(need: dict[str, Any], inputs: dict[str, Any], state: dict[str, Any] | None) -> str:
    for source in (
        need,
        inputs,
        dict((state or {}).get("current_route_context") or {}),
        dict(state or {}),
    ):
        for key in ("specId", "spec_id", "currentSpecId", "activeSpecId"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _bound_workspace_path_from_state(state: dict[str, Any] | None) -> str:
    runtime_context = get_runtime_context()
    for source in (
        dict((state or {}).get("current_route_context") or {}),
        dict(state or {}),
        runtime_context,
    ):
        workspace = str(source.get("workspacePath") or source.get("workspace_path") or "").strip()
        if workspace:
            return workspace
        binding = source.get("workspaceBinding") or source.get("workspace_binding")
        if isinstance(binding, dict):
            workspace = str(binding.get("workspacePath") or binding.get("workspace_path") or "").strip()
            if workspace:
                return workspace
    return ""


def _workspace_path_from_route(inputs: dict[str, Any], state: dict[str, Any] | None) -> str:
    bound_workspace = _bound_workspace_path_from_state(state)
    if bound_workspace:
        return bound_workspace
    workspace = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip()
    if workspace:
        return workspace
    return ""


def _same_workspace_path(left: str, right: str) -> bool:
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))
    except (OSError, RuntimeError, ValueError):
        return left.strip().casefold() == right.strip().casefold()


def _spec_workspace_path_from_route(inputs: dict[str, Any], state: dict[str, Any] | None) -> str:
    state_dict = dict(state or {})
    route_context = dict(state_dict.get("current_route_context") or {})
    for candidate in (
        state_dict.get("specBrief"),
        state_dict.get("spec_brief"),
        route_context.get("specBrief"),
        route_context.get("spec_brief"),
    ):
        if not isinstance(candidate, dict):
            continue
        workspace = str(
            candidate.get("workspacePath")
            or candidate.get("workspace_path")
            or candidate.get("projectRoot")
            or candidate.get("project_root")
            or ""
        ).strip()
        if workspace:
            return workspace
    runtime_context = get_runtime_context()
    for source in (route_context, state_dict, runtime_context, inputs):
        workspace = str(source.get("workspacePath") or source.get("workspace_path") or "").strip()
        if workspace:
            return workspace
    return ""


def _spec_runtime_execution_allowed(spec_brief: dict[str, Any]) -> bool:
    pipeline = dict(spec_brief.get("pipelineControl") or {})
    approved = {str(item).strip().lower() for item in list(spec_brief.get("approvedStages") or [])}
    return bool(pipeline.get("runtimeExecutionAllowed")) or {"requirements", "design", "tasks"}.issubset(approved)


def _split_spec_refs(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    refs = re.findall(r"\b(?:REQ|BFIX|DES|TASK|TSK|T|AC)-\d{2,}\b", text, flags=re.IGNORECASE)
    if refs:
        normalized: list[str] = []
        for ref in refs:
            item = ref.upper()
            match = re.match(r"^(?:TASK|TSK|T)-(\d+)$", item)
            if match:
                item = f"TASK-{int(match.group(1)):03d}"
            normalized.append(item)
        return list(dict.fromkeys(normalized))
    return []


def _extract_task_field(excerpt: str, labels: tuple[str, ...]) -> str:
    for label in labels:
        match = re.search(
            rf"(?im)^\s*(?:[-*]\s*)?(?:\*\*|`)?{re.escape(label)}(?:\*\*|`)?\s*[:：](?:\*\*)?\s*(.+?)\s*$",
            excerpt,
        )
        if match:
            return match.group(1).strip()
    for label in labels:
        match = re.search(
            rf"(?im)^\s*\|\s*(?:\*\*|`)?{re.escape(label)}(?:\*\*|`)?\s*\|\s*(.+?)\s*\|\s*$",
            excerpt,
        )
        if match:
            value = re.sub(r"`([^`]+)`", r"\1", match.group(1)).strip()
            return value.strip()
    return ""


def _extract_task_block(excerpt: str, labels: tuple[str, ...], *, limit: int = 1200) -> str:
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?ims)^\s*(?:[-*]\s*)?\*\*(?:{label_pattern})\*\*\s*[:：]?\s*\n(?P<body>.*?)(?=\n\s*(?:[-*]\s*)?\*\*[^*\n]+?\*\*\s*[:：]|\n---|\n###\s+|\Z)",
        excerpt,
    )
    if not match:
        return ""
    return _safe_compact_text(match.group("body"), limit=limit)


def _task_id_aliases(task_id: str) -> list[str]:
    normalized = str(task_id or "").strip().upper()
    aliases = [normalized] if normalized else []
    match = re.match(r"^TASK-(\d+)$", normalized)
    if match:
        number = int(match.group(1))
        aliases.extend([f"TSK-{number:03d}", f"T-{number:03d}", f"T-{number:02d}"])
    return list(dict.fromkeys([item for item in aliases if item]))


def _canonical_task_id(raw: Any, *, index: int = 0) -> str:
    text = str(raw or "").strip().upper()
    match = re.match(r"^(?:TASK|TSK|T)-(\d+)$", text)
    if match:
        return f"TASK-{int(match.group(1)):03d}"
    return f"TASK-{index + 1:03d}"


def _extract_task_ids_from_markdown(markdown: str) -> list[str]:
    text = str(markdown or "")
    seen: set[str] = set()
    ids: list[str] = []

    def add_from(pattern: str) -> None:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE):
            raw = match.group(1) if match.lastindex else match.group(0)
            task_id = _canonical_task_id(raw, index=len(ids))
            if task_id not in seen:
                seen.add(task_id)
                ids.append(task_id)

    add_from(r"^\s*#{2,6}\s+((?:TASK|TSK|T)-\d{2,})\b")
    add_from(r"^\s*[-*]\s*(?:\[[ xX]\]\s*)?((?:TASK|TSK|T)-\d{2,})\b")
    if ids:
        return ids
    pattern = re.compile(r"\b(?:TASK|TSK|T)-\d{2,}\b", flags=re.IGNORECASE)
    for match in pattern.finditer(text):
        task_id = _canonical_task_id(match.group(0), index=len(ids))
        if task_id not in seen:
            seen.add(task_id)
            ids.append(task_id)
    return ids


def _find_task_line_match(text: str, refs: list[str]) -> re.Match[str] | None:
    if not refs:
        return None
    ref_pattern = "|".join(re.escape(ref) for ref in refs)
    preferred_patterns = (
        rf"(?im)^\s*#{{2,6}}\s+(?:{ref_pattern})\b.*$",
        rf"(?im)^\s*(?:[-*]\s*(?:\[[ xX]\]\s*)?)(?:{ref_pattern})\b.*$",
    )
    for pattern in preferred_patterns:
        match = re.search(pattern, text)
        if match:
            return match
    for pattern in (
        rf"(?im)^\s*(?!\|.*~)(?:{ref_pattern})\b.*$",
        rf"(?im)^.*\b(?:{ref_pattern})\b.*$",
    ):
        match = re.search(pattern, text)
        if match:
            return match
    return None


def _task_sections_from_markdown(markdown: str, task_ids: list[str]) -> list[dict[str, Any]]:
    text = str(markdown or "")
    sections: list[dict[str, Any]] = []
    normalized_task_ids = [_canonical_task_id(task_id, index=index) for index, task_id in enumerate(task_ids)]
    heading_task_ids = _extract_task_ids_from_markdown(text)
    if heading_task_ids:
        normalized_task_ids = heading_task_ids
    if not normalized_task_ids:
        normalized_task_ids = _extract_task_ids_from_markdown(text)
    for index, task_id in enumerate(normalized_task_ids):
        normalized_id = _canonical_task_id(task_id, index=index)
        refs = _task_id_aliases(normalized_id)
        match = _find_task_line_match(text, refs)
        if not match:
            sections.append(
                {
                    "taskId": normalized_id,
                    "title": f"Execute approved {normalized_id}",
                    "excerpt": normalized_id,
                }
            )
            continue
        start = text.rfind("\n", 0, match.start()) + 1
        next_task = re.search(r"(?im)^\s*(?:#{2,6}\s*)?(?:[-*]\s*(?:\[[ xX]\]\s*)?)?(?:TASK|TSK|T)-\d{2,}\b", text[match.end() :])
        next_heading = re.search(r"(?m)^##+\s+", text[match.end() :])
        candidates = [len(text)]
        if next_task:
            candidates.append(match.end() + next_task.start())
        if next_heading:
            candidates.append(match.end() + next_heading.start())
        end = min(candidates)
        excerpt = text[start:end].strip()
        first_line = excerpt.splitlines()[0] if excerpt else normalized_id
        title = re.sub(r"(?i)^.*\b(?:TASK|TSK|T)-\d{2,}\b\s*[:：.\-、]?\s*", "", first_line).strip()
        title = re.sub(r"^\[[ xX]\]\s*", "", title).strip("-:： ")
        output_labels = (
            "expectedArtifacts",
            "expected artifacts",
            "expectedOutput",
            "expected output",
            "expected output path",
            "output path",
            "output",
            "输出",
            "产物",
            "预期输出",
            "预期输出路径",
        )
        output_file = _extract_task_block(excerpt, output_labels, limit=900)
        if not output_file:
            output_file = _extract_task_field(
            excerpt,
                output_labels,
            )
        if not output_file:
            output_match = re.search(
                r"(?ims)\*\*(?:输出文件|预期输出路径|预期输出|输出路径)\*\*\s*[:：]?\s*\n(?P<body>.*?)(?:\n---|\n###\s+|\Z)",
                excerpt,
            )
            if output_match:
                output_file = _safe_compact_text(output_match.group("body"), limit=900)
        acceptance = _extract_task_block(
            excerpt,
            ("acceptance", "acceptance / proof", "acceptance proof", "验收", "验收标准"),
            limit=1200,
        )
        if not acceptance:
            acceptance = _extract_task_field(excerpt, ("acceptance", "验收"))
        if not acceptance:
            acceptance_match = re.search(r"(?ims)\*\*验收标准\*\*\s*[:：]?\s*\n(?P<body>.*?)(?:\n---|\n###\s+|\Z)", excerpt)
            if acceptance_match:
                acceptance = _safe_compact_text(acceptance_match.group("body"), limit=1200)
        sections.append(
            {
                "taskId": normalized_id,
                "title": title or f"Execute approved {normalized_id}",
                "excerpt": _safe_compact_text(excerpt, limit=5000),
                "runtimeLane": _extract_task_field(
                    excerpt,
                    (
                        "runtimeLane",
                        "runtime lane",
                        "Runtime",
                        "Lane",
                        "执行泳道",
                        "执行通道",
                        "执行频道",
                        "执行方",
                        "执行角色",
                        "执行者",
                    ),
                ),
                "dependsOn": _split_spec_refs(_extract_task_field(excerpt, ("dependsOn", "depends on", "Depends", "依赖"))),
                "specRefs": _split_spec_refs(
                    " ".join(
                        [
                            _extract_task_field(excerpt, ("specRefs", "spec refs", "Refs", "引用")),
                            _extract_task_field(excerpt, ("需求引用", "requirement refs", "requirements")),
                            _extract_task_field(excerpt, ("设计引用", "design refs", "design")),
                        ]
                    )
                ),
                "inputRefs": _extract_task_field(excerpt, ("inputRefs", "input refs", "输入")),
                "expectedOutput": output_file,
                "acceptance": acceptance,
                "proofRequired": _extract_task_field(excerpt, ("proofRequired", "proof required", "proof", "证明")),
                "mvpSlice": _extract_task_field(excerpt, ("mvpSlice", "mvp slice", "MVP", "MVP 切片", "最小切片", "最小可验收切片")),
                "independentAcceptance": _extract_task_field(
                    excerpt,
                    ("independentAcceptance", "independent acceptance", "独立验收", "独立验收方式", "独立可验收"),
                ),
            }
        )
    return sections


def _approved_spec_execution_bundle(
    need: dict[str, Any],
    inputs: dict[str, Any],
    *,
    state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    spec_id = _spec_id_from_route_need(need, inputs, state)
    if not spec_id:
        return None
    workspace_path = _spec_workspace_path_from_route(inputs, state)
    if not workspace_path:
        return None
    try:
        detail = spec_service.read_spec(workspace_path=workspace_path, spec_id=spec_id, max_chars=80000)
    except Exception as exc:  # noqa: BLE001 - keep runtime route recoverable.
        return {
            "kind": "SpecExecutionBundle",
            "specId": spec_id,
            "workspacePath": workspace_path,
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    spec_brief = dict(detail.get("specBrief") or {})
    if not _spec_runtime_execution_allowed(spec_brief):
        return None
    stages = dict(detail.get("stages") or {})
    docs: dict[str, Any] = {}
    for stage in ("requirements", "bugfix", "design", "tasks"):
        payload = stages.get(stage)
        if not isinstance(payload, dict):
            continue
        docs[stage] = {
            "stage": stage,
            "detailRef": payload.get("documentRef") or f"spec://{spec_id}/{stage}",
            "relativePath": payload.get("relativePath"),
            "ids": list(payload.get("ids") or []),
            "content": _safe_compact_text(payload.get("content"), limit=12000 if stage == "tasks" else 9000),
            "truncated": bool(payload.get("truncated")),
        }
    task_doc = dict(docs.get("tasks") or {})
    traceability = dict(spec_brief.get("traceability") or {}) if isinstance(spec_brief.get("traceability"), dict) else {}
    task_sections = [
        dict(item)
        for item in list(traceability.get("tasks") or [])
        if isinstance(item, dict)
    ]
    parsed_task_sections = _task_sections_from_markdown(
        str(task_doc.get("content") or ""),
        list(task_doc.get("ids") or []),
    )
    if task_sections:
        parsed_by_id = {
            str(item.get("taskId") or "").strip(): item
            for item in parsed_task_sections
            if str(item.get("taskId") or "").strip()
        }
        merged_sections: list[dict[str, Any]] = []
        for item in task_sections:
            parsed = dict(parsed_by_id.get(str(item.get("taskId") or "").strip()) or {})
            for key, value in item.items():
                if value not in (None, "", [], {}):
                    parsed[key] = value
            merged_sections.append(parsed)
        task_sections = merged_sections
    else:
        task_sections = parsed_task_sections
    return {
        "kind": "SpecExecutionBundle",
        "status": "ready",
        "specId": spec_id,
        "featureName": spec_brief.get("featureName"),
        "workspacePath": workspace_path,
        "specDir": spec_brief.get("specDir"),
        "targetOutputDirectories": list(spec_brief.get("targetOutputDirectories") or [])[:8],
        "explicitDeliverableFiles": list(spec_brief.get("explicitDeliverableFiles") or [])[:16],
        "approvedStages": list(spec_brief.get("approvedStages") or []),
        "pipelineControl": dict(spec_brief.get("pipelineControl") or {}),
        "qualityEvidence": spec_brief.get("qualityEvidence") if isinstance(spec_brief.get("qualityEvidence"), dict) else {},
        "annexDocuments": spec_brief.get("annexDocuments") if isinstance(spec_brief.get("annexDocuments"), dict) else {},
        "linkedSections": list(spec_brief.get("linkedSections") or [])[:24],
        "documents": docs,
        "tasks": task_sections,
        "traceability": {
            "frameworkDigest": traceability.get("frameworkDigest"),
            "missingRefs": list(traceability.get("missingRefs") or [])[:20],
            "distributionChecks": traceability.get("distributionChecks") if isinstance(traceability.get("distributionChecks"), dict) else {},
        },
        "distribution": {
            "strategy": "task_sliced_with_stage_context",
            "mainRuntimeReceives": ["SpecExecutionBundle", "all approved stage summaries/content", "task briefs"],
            "subagentReceives": [
                "assigned task excerpt",
                "linked requirement/design refs",
                "detailRefs",
                "engineeringExecutionContract",
                "handoffContract",
            ],
            "grandchildReceives": [
                "parent task slice",
                "required refs only",
                "allowedWorkset/forbiddenScopes",
                "handoffRequired",
            ],
        },
    }


def _required_runtime_access_from_spec_bundle(bundle: dict[str, Any], kind: str) -> list[str]:
    lanes = " ".join(
        str(task.get("runtimeLane") or task.get("excerpt") or "")
        for task in list(bundle.get("tasks") or [])
        if isinstance(task, dict)
    ).lower()
    groups: list[str] = []
    if kind == "engineering":
        groups.append("delegation.recursive")
    if any(token in lanes for token in ("research", "调研", "evidence", "source")):
        groups.append("research.core")
    if any(token in lanes for token in ("delegation", "subagent", "agent", "子agent", "孙agent", "并行")):
        groups.append("delegation.recursive")
    return list(dict.fromkeys(groups))


def _spec_task_runtime_family(task: dict[str, Any], route_kind: str) -> str:
    lane = str(task.get("runtimeLane") or "").strip().lower()
    probe = " ".join(
        [
            str(task.get("title") or ""),
            str(task.get("excerpt") or ""),
            str(task.get("expectedOutput") or ""),
        ]
    ).lower()
    if "research" in lane or "调研" in lane:
        return "research"
    if "engineering" in lane or "工程" in lane:
        return "engineering"
    if "qa" in lane or "test" in lane or "验证" in lane:
        return "engineering"
    if "creative" in lane or "media" in lane or "创意" in lane:
        return "creative_media"
    if "delegation" in lane or "subagent" in lane or "子agent" in lane or "孙agent" in lane:
        return "delegation"
    strong_engineering_markers = (
        "skill.md",
        "verification-report",
        "delivery-summary",
        "quality_check.py",
        "merge_research.py",
        "目录初始化",
        "脚本复制",
        "创建目录",
        "创建完整",
        "skill构建",
        "skill 构建",
        "质量验证",
        "最终交付",
        "交付文档",
        "构建与质量自检",
    )
    if any(token in probe for token in strong_engineering_markers):
        return "engineering"
    if "supervisor" in lane or "governance" in lane or "主管" in lane:
        return "governance"
    if any(token in probe for token in ("research", "调研", "source", "evidence", "citation", "来源")):
        return "research"
    if any(token in probe for token in ("delegation", "subagent", "agent swarm", "子agent", "孙agent", "并行子")):
        return "research" if "research" in probe or "调研" in probe else "delegation"
    if any(token in probe for token in ("creative", "media", "image", "video", "audio", "素材", "视频", "图片")):
        return "creative_media"
    if any(token in probe for token in ("governance", "supervisor", "验收", "确认", "检查点")):
        return "governance"
    if any(token in probe for token in ("engineering", "工程", "write", "file", "artifact", "skill.md", "目录", "构建", "验证")):
        return "engineering"
    return _normalize_capability_kind(route_kind) or "engineering"


def _spec_task_required_capabilities(family: str, *, writes_artifact: bool) -> list[str]:
    if family == "research":
        capabilities = ["source_backed_research", "evidence_pack", "research_handoff"]
        if writes_artifact:
            capabilities.append("workspace_mutation")
        return capabilities
    if family == "creative_media":
        return ["creative_asset_request", "artifact_handoff"]
    if family == "delegation":
        return ["delegation", "handoff"]
    if family == "governance":
        return ["spec_section_read", "verification", "handoff_reconciliation"]
    capabilities = ["spec_section_read", "verification", "proof_handoff"]
    if writes_artifact:
        capabilities.append("workspace_mutation")
    return capabilities


def _spec_task_execution_lane(family: str) -> str:
    if family in {"research", "delegation", "governance"}:
        return "subagent"
    if family == "creative_media":
        return "auto"
    return "engineering"


def _spec_task_deliverable_kind(family: str) -> str:
    if family == "research":
        return "evidence"
    if family == "creative_media":
        return "artifact"
    if family == "delegation":
        return "handoff"
    if family == "governance":
        return "verification"
    return "artifact"


def _spec_task_is_verification_intent(task: dict[str, Any]) -> bool:
    probe = " ".join(
        str(task.get(key) or "")
        for key in ("title", "runtimeLane")
    ).lower()
    return any(
        marker in probe
        for marker in (
            "验收",
            "验证",
            "测试",
            "检查",
            "校验",
            "审计",
            "verify",
            "verification",
            "test",
            "validate",
            "audit",
            "quality check",
        )
    )


def _spec_task_writes_artifact(task: dict[str, Any], family: str) -> bool:
    if family == "creative_media":
        return True
    if family == "research":
        output = str(
            task.get("expectedArtifacts")
            or task.get("expectedOutput")
            or task.get("expectedOutputs")
            or task.get("output")
            or ""
        )
        text = " ".join(
            str(task.get(key) or "")
            for key in ("taskId", "title", "excerpt", "taskExcerpt", "expectedOutput", "expectedOutputs", "acceptance", "proofRequired")
        )
        return bool(_spec_task_expected_paths(output)) or any(
            marker in text.lower()
            for marker in ("write artifact", "create artifact", "写入", "创建文件", "生成文件")
        )
    if family != "engineering":
        return False
    lane = str(task.get("runtimeLane") or "").strip().lower()
    expected_output = str(
        task.get("expectedArtifacts")
        or task.get("expectedOutput")
        or task.get("expectedOutputs")
        or task.get("output")
        or ""
    )
    text = " ".join(
        str(task.get(key) or "")
        for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
    ).lower()
    if (
        _spec_task_is_verification_intent(task)
        and not _spec_task_has_explicit_output_path(expected_output)
    ):
        return False
    # An explicit output directory is still a bounded write contract.  Do not
    # downgrade directory creation to a read-only task merely because it has
    # no file extension; the Spec author already supplied the exact target.
    if _spec_task_expected_paths(expected_output):
        return True
    # Verification/checkpoint/final-summary tasks may inspect artifacts or produce
    # user-visible summaries, but they should not be treated as content-writing
    # workers unless a concrete artifact path is present.
    if any(marker in lane for marker in ("verification", "governance", "supervisor")):
        return bool(re.search(r"(?i)(?:skill\.md|[\\/\w.-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml))", text))
    if re.search(r"(?i)(?:skill\.md|[\\/\w.-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml))", text):
        return True
    if any(marker in text for marker in ("最终交付摘要", "交付整理")):
        return False
    return any(
        marker in text
        for marker in (
            "写入",
            "创建完整",
            "组装构建",
            "skill构建",
            "skill 构建",
            "build skill",
            "assemble skill",
            "write artifact",
        )
    )


def _spec_task_validates_skill_artifact(task: dict[str, Any], family: str, *, writes_artifact: bool) -> bool:
    if family != "engineering":
        return False
    text = " ".join(
        str(task.get(key) or "")
        for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
    ).lower()
    validation_markers = ("质量验证", "交付前质量验证", "validate", "validation")
    skill_build_markers = (
        "skill.md",
        "skill 构建",
        "skill构建",
        "生成 skill",
        "生成skill",
        "组装 skill",
        "组装skill",
        "build skill",
        "assemble skill",
        "write skill",
        "skill artifact",
    )
    if "skill.md" in text:
        return writes_artifact or any(marker in text for marker in validation_markers)
    if any(marker in text for marker in skill_build_markers):
        return True
    return "skill" in text and any(marker in text for marker in validation_markers)


_SPEC_OUTPUT_PATH_PATTERN = re.compile(
    r"(?i)(?:`([^`]+)`|(?<![\w.-])([\w@.$~][\w@.$~\-/\\]*(?:\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml|png|jpg|jpeg|webp|svg|mp3|wav|mp4|mov)|[\\/])))(?![\w.-])"
)


def _spec_task_has_explicit_output_path(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if any(_spec_task_expected_paths(match.group(1)) for match in re.finditer(r"`([^`]+)`", text)):
        return True
    path_token = r"[\w@.$~][\w@.$~\-/\\]*\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml|png|jpg|jpeg|webp|svg|mp3|wav|mp4|mov)"
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", raw_line).strip().strip("'\"")
        if re.fullmatch(rf"{path_token}(?:\s*(?:,|、|\band\b|\bor\b|和|及)\s*{path_token})*", line, flags=re.IGNORECASE):
            return True
        path_match = re.search(path_token, line, flags=re.IGNORECASE)
        if path_match and re.fullmatch(
            r"(?i)(?:single\s+file|output\s+file|report\s+file|单文件|输出文件|报告文件|日志文件)?\s*",
            line[: path_match.start()],
        ):
            return True
    return False


def _spec_task_expected_paths(*values: Any) -> list[str]:
    """Extract likely artifact paths from a compact task section.

    The result is intentionally conservative: it is a workset hint for
    delegated agents, not a filesystem permission grant by itself.
    """

    paths: list[str] = []
    for value in values:
        text = str(value or "")
        if not text:
            continue
        for match in _SPEC_OUTPUT_PATH_PATTERN.finditer(text):
            candidate = str(match.group(1) or match.group(2) or "").strip()
            if not candidate:
                continue
            candidate = candidate.strip("`'\"，,。;；:：")
            lowered = candidate.lower()
            if (
                lowered in {"http://", "https://", "file://"}
                or lowered.startswith(("http://", "https://", "file://", "spec://"))
                or any(marker in candidate for marker in ("<", ">", "\r", "\n"))
            ):
                continue
            if not re.search(r"(?i)([\\/]|(?:^|[\\/])?[\w@.$~-]+\.(?:md|txt|json|py|ts|tsx|js|jsx|html|css|yml|yaml|png|jpg|jpeg|webp|svg|mp3|wav|mp4|mov)$)", candidate):
                continue
            if candidate not in paths:
                paths.append(candidate)
    return paths[:16]


def _resolve_spec_expected_paths(
    expected_paths: list[str],
    target_output_directories: list[Any],
) -> list[str]:
    """Resolve bare output filenames against one authoritative Spec target.

    The output filenames have already been extracted from explicit task output
    fields.  The target directory comes from the human contract stored in the
    Spec manifest.  We deliberately do not guess when either side is
    ambiguous.
    """

    targets = [
        str(value or "").strip().strip("`'\"").replace("\\", "/").rstrip("/")
        for value in target_output_directories
        if str(value or "").strip()
    ]
    targets = list(dict.fromkeys(target for target in targets if target))
    if len(targets) != 1:
        return list(expected_paths)

    target = targets[0]
    resolved: list[str] = []
    for value in expected_paths:
        candidate = str(value or "").strip().replace("\\", "/")
        if candidate and "/" not in candidate:
            candidate = f"{target}/{candidate}"
        if candidate and candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _spec_stage_slice(markdown: Any, refs: list[str], *, stage: str, limit: int = 5200) -> str:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized_refs = [str(ref or "").strip().upper() for ref in refs if str(ref or "").strip()]
    if not text or not normalized_refs:
        return ""
    headings = list(re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", text))
    selected: list[str] = []
    for index, match in enumerate(headings):
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = text[start:end].strip()
        upper_block = block.upper()
        title = str(match.group(1) or "").strip()
        include = any(ref in upper_block for ref in normalized_refs)
        if not include and stage == "design":
            heading_number = re.match(r"^\s*(\d+)(?:\.\d+)*[.、:\s]", title)
            if heading_number:
                section_number = int(heading_number.group(1))
                include = any(
                    (ref_number := re.search(r"(\d+)$", ref)) is not None
                    and int(ref_number.group(1)) == section_number
                    for ref in normalized_refs
                )
        if include and block not in selected:
            selected.append(block)
    if not selected:
        return ""
    return _safe_compact_text("\n\n".join(selected), limit=limit)


def _spec_shared_stage_context(markdown: Any, *, stage: str, limit: int = 4200) -> str:
    text = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    if stage == "requirements":
        # Requirements usually carry the target subject, scope, output path,
        # and hard constraints shared by every task. Keep it compact, but do
        # not slice it by task ID; otherwise public context can accidentally
        # become private to one task.
        return _safe_compact_text(text, limit=limit)
    if stage == "design":
        headings = list(re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", text))
        selected: list[str] = []
        for index, match in enumerate(headings):
            title = str(match.group(1) or "").strip()
            if not re.search(r"(?i)(总体|架构|信息流|调研策略|source|strategy|runtime|design|框架|文件结构)", title):
                continue
            start = match.start()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            block = text[start:end].strip()
            if block and block not in selected:
                selected.append(block)
        if selected:
            return _safe_compact_text("\n\n".join(selected), limit=limit)
    return _safe_compact_text(text, limit=limit)


def _spec_shared_execution_context(
    *,
    spec_id: str,
    workspace_path: str,
    requirement_doc: dict[str, Any],
    design_doc: dict[str, Any],
    framework_digest: str,
) -> str:
    requirements = _spec_shared_stage_context(requirement_doc.get("content"), stage="requirements", limit=4600)
    design = _spec_shared_stage_context(design_doc.get("content"), stage="design", limit=3200)
    parts = [
        f"Current active specId: {spec_id}" if spec_id else "",
        f"Workspace: {workspace_path}" if workspace_path else "",
        f"Framework / architecture everyone must follow: {framework_digest}" if framework_digest else "",
        f"Shared requirements for every task:\n{requirements}" if requirements else "",
        f"Shared design constraints for every task:\n{design}" if design else "",
    ]
    return _safe_compact_text("\n\n".join(part for part in parts if part), limit=7600)


def _spec_task_route_query(
    *,
    task: dict[str, Any],
    shared_context: str,
    requirement_slice: str,
    design_slice: str,
    limit: int = 3600,
) -> str:
    # This is used only by Extensions/Skill/MCP prefiltering. It must preserve
    # the task's real subject and method anchors, while avoiding raw JSON.
    parts = [
        str(task.get("title") or task.get("taskId") or "").strip(),
        str(task.get("taskExcerpt") or task.get("excerpt") or "").strip(),
        f"Shared Spec context:\n{shared_context}" if shared_context else "",
        f"Task requirement slice:\n{requirement_slice}" if requirement_slice else "",
        f"Task design slice:\n{design_slice}" if design_slice else "",
    ]
    return _safe_compact_text("\n\n".join(part for part in parts if part), limit=limit)


def _preferred_agent_for_spec_task(
    task: dict[str, Any],
    *,
    family: str,
    writes_artifact: bool,
    validates_skill_artifact: bool,
    expected_paths: list[str],
) -> str:
    if validates_skill_artifact:
        return "skill-workflow-curator"
    if family == "research":
        text = " ".join(
            str(task.get(key) or "")
            for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
        ).lower()
        if not writes_artifact and _spec_task_is_verification_intent(task):
            return "verification-engineer"
        return "web-research-architect"
    if family != "engineering":
        return ""
    text = " ".join(
        str(task.get(key) or "")
        for key in ("taskId", "title", "excerpt", "expectedOutput", "acceptance", "proofRequired")
    ).lower()
    suffixes = {Path(path.rstrip("/\\")).suffix.lower() for path in expected_paths if path.rstrip("/\\")}
    if not writes_artifact and _spec_task_is_verification_intent(task):
        return "verification-engineer"
    if suffixes.intersection({".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}):
        return "frontend-product-engineer"
    if suffixes and suffixes.issubset({".md", ".mdx", ".txt", ".rst"}):
        return "docs-delivery-writer"
    if writes_artifact or expected_paths or any(
        marker in text
        for marker in ("创建目录", "目录初始化", "mkdir", "implement", "write", "create", "实现", "写入", "编写")
    ):
        return "implementation-engineer"
    if any(marker in text for marker in ("规划", "拆解", "方案", "plan", "decompose")):
        return "implementation-engineer"
    return "implementation-engineer"


def _spec_task_engineering_execution_contract(
    *,
    spec_id: str,
    workspace_path: str,
    task_id: str,
    task: dict[str, Any],
    family: str,
    writes_artifact: bool,
    requirement_doc: dict[str, Any],
    design_doc: dict[str, Any],
    task_doc: dict[str, Any],
    spec_refs: list[str],
    expected_paths: list[str],
    acceptance: str,
    proof: str,
) -> dict[str, Any]:
    detail_refs = [
        ref
        for ref in [
            requirement_doc.get("detailRef"),
            design_doc.get("detailRef"),
            f"spec://{spec_id}/tasks#{task_id}",
        ]
        if ref
    ]
    # A workspace root is an execution boundary, not an implicit write grant.
    # Write authority must remain bounded to concrete output paths declared by
    # the approved task contract.
    allowed_workset = list(expected_paths or [])
    source_refs = {
        "specId": spec_id,
        "taskId": task_id,
        "requirementIds": [
            ref
            for ref in spec_refs
            if str(ref).upper().startswith(("REQ-", "BFIX-"))
            or re.match(r"^\d{1,2}\.\d{1,2}$", str(ref))
        ],
        "designIds": [ref for ref in spec_refs if str(ref).upper().startswith("DES-")],
        "detailRefs": detail_refs,
    }
    return {
        "workspacePath": workspace_path,
        "taskId": task_id,
        "runtimeFamily": family,
        "writeRequired": bool(writes_artifact),
        "allowedWorkset": allowed_workset,
        "expectedArtifacts": list(expected_paths or []),
        "sourceRefs": source_refs,
        "mustRead": [
            "Read the assigned task excerpt first.",
            "Use detailRefs to read approved requirements/design/task sections when the compact brief is insufficient.",
        ],
        "acceptance": [item for item in [acceptance, proof] if item],
        "forbiddenScopes": [
            "Do not read/write outside the Active Workspace Root unless another root is explicitly granted.",
            "Do not edit files outside allowedWorkset when concrete expected artifacts are listed.",
            "Do not use older specs, memory, or chat history to override the approved current Spec.",
            (
                "Do not execute generated or workspace file contents through eval, exec, encoded commands, or reflective "
                "loaders for verification; use read-only static checks or an already-approved browser/runtime tool."
            ),
            "Do not perform destructive commands or cross-project changes without approval.",
        ],
    }


def _spec_task_handoff_contract(*, spec_id: str, task_id: str, writes_artifact: bool) -> dict[str, Any]:
    required_fields = [
        "status",
        "specId",
        "taskId",
        "summary",
        "changedFiles",
        "commandsRun",
        "testResults",
        "artifacts",
        "proofRefs",
        "blockers",
        "residualRisks",
    ]
    return {
        "type": "engineering_typed_handoff",
        "requiredFields": required_fields,
        "completionRule": (
            "Return a typed handoff with verifiable proof/artifact/test result. "
            "A plain 'done' message is not enough."
        ),
        "mustInclude": [
            f"specId={spec_id}",
            f"taskId={task_id}",
            "what changed and why",
            "verification commands/results, including skipped or failed checks",
            "artifact/proof/detail refs when available",
        ],
        "writeRequired": bool(writes_artifact),
    }


def _brief_family_hint(brief: dict[str, Any]) -> str:
    family = str(brief.get("familyHint") or "").strip().lower()
    if family:
        return family
    capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), dict) else {}
    lane = str(capsule.get("runtimeLane") or "").strip().lower()
    if "research" in lane or "调研" in lane:
        return "research"
    if "delegation" in lane or "subagent" in lane or "子agent" in lane or "孙agent" in lane:
        return "delegation"
    if "creative" in lane or "media" in lane or "创意" in lane:
        return "creative_media"
    return "engineering"


def _canonical_spec_detail_ref(spec_id: str, detail_ref: Any, *, fallback_stage: str = "", fallback_id: str = "") -> str:
    ref = str(detail_ref or "").strip()
    spec = str(spec_id or "").strip()
    if not ref and fallback_stage and fallback_id:
        ref = f"spec://{spec}/{fallback_stage}#{fallback_id}" if spec else f"spec://{fallback_stage}#{fallback_id}"
    if spec and ref.startswith("spec://") and not ref.startswith(f"spec://{spec}/"):
        suffix = ref[len("spec://") :]
        if suffix.startswith("/"):
            suffix = suffix[1:]
        if suffix and "/" not in suffix.split("#", 1)[0]:
            return f"spec://{spec}/{suffix}"
    return ref


def _shape_spec_task_briefs_for_route(briefs: list[dict[str, Any]], *, kind: str, spec_id: str, workspace_path: str) -> list[dict[str, Any]]:
    if not briefs:
        return briefs
    normalized_kind = _normalize_capability_kind(kind)
    if normalized_kind in {"engineering", "delegation"}:
        # Spec tasks are the approved delivery contract. The route layer must
        # not merge, drop, or rename them; dependency-aware execution and UI
        # compaction belong to lower layers.
        return briefs
    return briefs


def _task_briefs_from_spec_bundle(bundle: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    spec_id = str(bundle.get("specId") or "").strip()
    workspace_path = str(bundle.get("workspacePath") or "").strip()
    docs = dict(bundle.get("documents") or {})
    traceability = dict(bundle.get("traceability") or {}) if isinstance(bundle.get("traceability"), dict) else {}
    framework_digest = str(traceability.get("frameworkDigest") or "").strip()
    target_output_directories = list(bundle.get("targetOutputDirectories") or [])
    requirement_doc = dict(docs.get("requirements") or docs.get("bugfix") or {})
    design_doc = dict(docs.get("design") or {})
    task_doc = dict(docs.get("tasks") or {})
    shared_spec_context = _spec_shared_execution_context(
        spec_id=spec_id,
        workspace_path=workspace_path,
        requirement_doc=requirement_doc,
        design_doc=design_doc,
        framework_digest=framework_digest,
    )
    tasks = [item for item in list(bundle.get("tasks") or []) if isinstance(item, dict)]
    if not tasks:
        tasks = [
            {
                "taskId": "TASK-001",
                "title": f"Execute approved Spec {spec_id}",
                "excerpt": "Execute the approved Spec and return typed handoff/proof.",
                "runtimeLane": kind,
                "specRefs": list(requirement_doc.get("ids") or [])[:8] + list(design_doc.get("ids") or [])[:8],
            }
        ]
    briefs: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_id = str(task.get("taskId") or f"TASK-{index + 1:03d}").strip().upper().replace("TSK-", "TASK-")
        lane = str(task.get("runtimeLane") or kind or "engineering").strip()
        family = _spec_task_runtime_family(task, kind)
        if family == "governance" and kind != "governance":
            continue
        explicit_output_values = [
            task.get("expectedArtifacts"),
            task.get("expectedOutput"),
            task.get("expectedOutputs"),
            task.get("output"),
        ]
        output = str(next((value for value in explicit_output_values if value not in (None, "", [], {})), "")).strip()
        acceptance = str(task.get("acceptance") or task.get("acceptanceProof") or "").strip()
        proof = str(task.get("proofRequired") or task.get("proof") or "").strip()
        mvp_slice = str(task.get("mvpSlice") or task.get("mvp") or "").strip()
        independent_acceptance = str(task.get("independentAcceptance") or "").strip()
        requirement_refs = list(task.get("requirementRefs") or [])
        design_refs = list(task.get("designRefs") or [])
        explicit_spec_refs = list(task.get("specRefs") or [])
        if explicit_spec_refs:
            spec_refs = explicit_spec_refs
        elif requirement_refs or design_refs:
            spec_refs = requirement_refs + design_refs
        else:
            spec_refs = list(requirement_doc.get("ids") or [])[:8] + list(design_doc.get("ids") or [])[:8]
        writes_artifact = _spec_task_writes_artifact(task, family)
        expected_paths = _spec_task_expected_paths(*explicit_output_values) if writes_artifact else []
        expected_paths = _resolve_spec_expected_paths(expected_paths, target_output_directories)
        if not output and expected_paths:
            output = "; ".join(expected_paths)
        validates_skill_artifact = _spec_task_validates_skill_artifact(task, family, writes_artifact=writes_artifact)
        allowed_write_set = list(expected_paths)
        execution_contract = _spec_task_engineering_execution_contract(
            spec_id=spec_id,
            workspace_path=workspace_path,
            task_id=task_id,
            task=task,
            family=family,
            writes_artifact=writes_artifact,
            requirement_doc=requirement_doc,
            design_doc=design_doc,
            task_doc=task_doc,
            spec_refs=spec_refs,
            expected_paths=expected_paths,
            acceptance=acceptance,
            proof=proof,
        )
        handoff_contract = _spec_task_handoff_contract(
            spec_id=spec_id,
            task_id=task_id,
            writes_artifact=writes_artifact,
        )
        child_signal = re.search(
            r"(?i)sub\s*agent|sub-agent|worker|parallel|fanout|子\s*agent|孙\s*agent|子agent|孙agent|并行",
            " ".join([lane, str(task.get("title") or ""), str(task.get("taskExcerpt") or task.get("excerpt") or "")]),
        )
        allow_child = bool(
            child_signal
            and family in {"delegation", "engineering", "research"}
            and not (family == "research" and re.search(r"(?i)调研 Agent|research agent", str(task.get("title") or "")))
        )
        requirement_snippet_text = " / ".join(
            f"{snippet.get('id')}: {snippet.get('summary')}"
            for snippet in list(task.get("requirementSnippets") or [])[:6]
            if isinstance(snippet, dict) and snippet.get("summary")
        )
        design_snippet_text = " / ".join(
            f"{snippet.get('title') or snippet.get('id')}: {snippet.get('summary')}"
            for snippet in list(task.get("designSnippets") or [])[:4]
            if isinstance(snippet, dict) and snippet.get("summary")
        )
        task_detail_ref = _canonical_spec_detail_ref(
            spec_id,
            task.get("detailRef"),
            fallback_stage="tasks",
            fallback_id=task_id,
        )
        requirement_ids = requirement_refs or [
            ref for ref in spec_refs if str(ref).upper().startswith(("REQ-", "BFIX-"))
        ]
        design_ids = design_refs or [ref for ref in spec_refs if str(ref).upper().startswith("DES-")]
        approved_requirement_slice = _spec_stage_slice(
            requirement_doc.get("content"),
            requirement_ids,
            stage="requirements",
        )
        approved_design_slice = _spec_stage_slice(
            design_doc.get("content"),
            design_ids,
            stage="design",
        )
        spec_document_paths = {
            key: value
            for key, value in {
                "requirements": requirement_doc.get("relativePath"),
                "design": design_doc.get("relativePath"),
                "tasks": task_doc.get("relativePath"),
            }.items()
            if value
        }
        preferred_agent_id = _preferred_agent_for_spec_task(
            task,
            family=family,
            writes_artifact=writes_artifact,
            validates_skill_artifact=validates_skill_artifact,
            expected_paths=expected_paths,
        )
        spec_execution_summary = _safe_compact_text(
            "\n".join(
                item
                for item in [
                    f"Shared Spec context: {shared_spec_context}" if shared_spec_context else "",
                    f"Framework / architecture everyone must follow: {framework_digest}" if framework_digest else "",
                    f"Task: {task.get('taskExcerpt') or task.get('excerpt') or task.get('title') or task_id}",
                    f"Requirements: {requirement_snippet_text}" if requirement_snippet_text else "",
                    f"Design: {design_snippet_text}" if design_snippet_text else "",
                ]
                if item
            ),
            limit=4200,
        )
        extensions_route_query = _spec_task_route_query(
            task=task,
            shared_context=shared_spec_context,
            requirement_slice=approved_requirement_slice,
            design_slice=approved_design_slice,
        )
        context = {
            "source": "approved_spec_execution_bundle",
            "specId": spec_id,
            "taskId": task_id,
            "taskDetailRef": task_detail_ref,
            "taskExcerpt": task.get("taskExcerpt") or task.get("excerpt") or task.get("title") or task_id,
            "sharedSpecContext": shared_spec_context,
            "extensionsRouteQuery": extensions_route_query,
            "specExecutionSummary": spec_execution_summary,
            "frameworkDigest": framework_digest,
            "approvedRequirementSlice": approved_requirement_slice,
            "approvedDesignSlice": approved_design_slice,
            "specDocumentPaths": spec_document_paths,
            "specRefUsage": (
                "spec:// refs are traceability identifiers, not URLs. Never pass them to curl, web tools, or shell commands. "
                "Use the approved slices attached here; if more context is needed, read the listed workspace-relative Spec document path."
            ),
            "runtimeLane": lane,
            "specRefs": spec_refs,
            "mvpSlice": mvp_slice,
            "independentAcceptance": independent_acceptance,
            "qualityEvidence": bundle.get("qualityEvidence") if isinstance(bundle.get("qualityEvidence"), dict) else {},
            "annexDocuments": bundle.get("annexDocuments") if isinstance(bundle.get("annexDocuments"), dict) else {},
            "linkedSections": list(bundle.get("linkedSections") or [])[:24],
            "engineeringExecutionContract": execution_contract,
            "handoffContract": handoff_contract,
            "stageRefs": {
                "requirements": requirement_doc.get("detailRef"),
                "design": design_doc.get("detailRef"),
                "tasks": task_doc.get("detailRef"),
            },
            "stageContent": {
                "requirements": requirement_doc.get("content"),
                "design": design_doc.get("content"),
                "tasks": task_doc.get("content"),
            },
            "specExecutionBundle": {
                key: value
                for key, value in bundle.items()
                if key not in {"documents"}
            },
        }
        brief = normalize_task_brief(
            {
                "taskBriefId": task_id,
                "title": task.get("title") or task_id,
                "goal": f"{task_id}: {task.get('title') or 'Execute approved Spec task'}",
                "context": context,
                "routeQuery": extensions_route_query,
                "writeSet": allowed_write_set,
                "expectedOutputs": allowed_write_set,
                "behaviorScope": ["approved_spec_execution", "runtime_first", "verification"],
                "requiredCapabilities": _spec_task_required_capabilities(family, writes_artifact=writes_artifact),
                "acceptanceContract": {
                    "must": [
                        f"Execute approved {task_id} only within the approved Spec scope.",
                        "Use the linked requirements/design/task refs as the source of truth.",
                        "Return a typed handoff with specId, taskId, touched artifacts, and proof or degraded blocker.",
                        *([f"Expected output: {output}"] if output else []),
                        *([f"Acceptance: {acceptance}"] if acceptance else []),
                        *([f"Proof required: {proof}"] if proof else []),
                        *([f"MVP slice: {mvp_slice}"] if mvp_slice else []),
                        *([f"Independent acceptance: {independent_acceptance}"] if independent_acceptance else []),
                    ],
                    "should": [
                        "Read exact detailRef sections when the compact excerpt is insufficient.",
                        "Do not use older specs or memory to override the approved current Spec.",
                    ],
                    "nice": [],
                },
                "dependency": list(task.get("dependsOn") or []),
                "parallelGroup": lane or kind,
                "executionLaneHint": _spec_task_execution_lane(family),
                "familyHint": "" if family == "governance" else family,
                **({"preferredAgentId": preferred_agent_id} if preferred_agent_id else {}),
                "deliverableKind": "skill_artifact" if validates_skill_artifact else _spec_task_deliverable_kind(family),
                "writeRequired": writes_artifact,
                **({"validateSkillArtifact": True} if validates_skill_artifact else {}),
                "allowChildDelegation": allow_child,
                "childDelegationBudget": {"maxDepth": 1, "inherits": ["taskId", "specId", "specRefs", "detailRefs"]} if allow_child else {},
                "specRefs": {
                    "specId": spec_id,
                    "taskId": task_id,
                    "requirementIds": requirement_refs
                    or requirement_ids,
                    "designIds": design_refs or design_ids,
                    "detailRefs": [
                        ref
                        for ref in [
                            requirement_doc.get("detailRef"),
                            design_doc.get("detailRef"),
                            task_detail_ref,
                        ]
                        if ref
                    ],
                },
                "engineeringTaskCapsule": {
                    "deliverableKind": "skill_artifact" if validates_skill_artifact else _spec_task_deliverable_kind(family),
                    "writeRequired": writes_artifact,
                    **({"validateSkillArtifact": True} if validates_skill_artifact else {}),
                    "specId": spec_id,
                    "taskId": task_id,
                    "requirementIds": requirement_refs
                    or [ref for ref in spec_refs if str(ref).upper().startswith(("REQ-", "BFIX-"))],
                    "designIds": design_refs or [ref for ref in spec_refs if str(ref).upper().startswith("DES-")],
                    "frameworkDigest": framework_digest,
                    "runtimeLane": lane,
                    "workspacePath": workspace_path,
                    "writeSet": allowed_write_set,
                    "allowedWorkset": list(execution_contract.get("allowedWorkset") or []),
                    "expectedArtifacts": list(execution_contract.get("expectedArtifacts") or []),
                    "forbiddenScopes": list(execution_contract.get("forbiddenScopes") or []),
                    "proofExpectations": [
                        "Report exact touched files/artifacts.",
                        "Reference approved specId and taskId.",
                        "Attach verification result or recoverable blocker.",
                        *([f"Independent acceptance: {independent_acceptance}"] if independent_acceptance else []),
                    ],
                    "handoffRequired": list(handoff_contract.get("requiredFields") or []),
                },
                "proofExpectations": [
                    "Typed runtime handoff",
                    "Touched files/artifacts",
                    "Verification proof or degraded blocker",
                ],
            }
        )
        briefs.append(brief)
    return _shape_spec_task_briefs_for_route(briefs, kind=kind, spec_id=spec_id, workspace_path=workspace_path)


_SPEC_TASK_REF_KEYS = (
    "taskRef",
    "taskRefs",
    "taskId",
    "taskIds",
    "specTaskRef",
    "specTaskRefs",
    "specTaskId",
    "specTaskIds",
)


def _normalize_spec_task_ref(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\b(?:TASK|TSK|T)[\s_-]*(\d{1,6})\b", text)
    if not match:
        return ""
    number = int(match.group(1))
    width = max(3, len(match.group(1)))
    return f"TASK-{number:0{width}d}"


def _iter_spec_task_ref_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values: list[Any] = []
        for item in value:
            values.extend(_iter_spec_task_ref_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for key in _SPEC_TASK_REF_KEYS:
            if key in value:
                values.extend(_iter_spec_task_ref_values(value.get(key)))
        return values
    text = str(value or "").strip()
    if not text:
        return []
    matches = re.findall(r"\b(?:TASK|TSK|T)[\s_-]*\d{1,6}\b", text, flags=re.IGNORECASE)
    return matches or [text]


def _requested_spec_task_refs(need: dict[str, Any], inputs: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for source in (need, inputs):
        if not isinstance(source, dict):
            continue
        for key in _SPEC_TASK_REF_KEYS:
            for value in _iter_spec_task_ref_values(source.get(key)):
                ref = _normalize_spec_task_ref(value)
                if ref and ref not in refs:
                    refs.append(ref)
    return refs


def _spec_task_ref_from_brief(brief: dict[str, Any]) -> str:
    candidates: list[Any] = [
        brief.get("taskBriefId"),
        brief.get("taskId"),
        brief.get("id"),
    ]
    context = brief.get("context") if isinstance(brief.get("context"), dict) else {}
    candidates.extend([context.get("taskId"), context.get("taskRef")])
    spec_refs = brief.get("specRefs") if isinstance(brief.get("specRefs"), dict) else {}
    candidates.extend([spec_refs.get("taskId"), spec_refs.get("taskRef")])
    capsule = brief.get("engineeringTaskCapsule") if isinstance(brief.get("engineeringTaskCapsule"), dict) else {}
    candidates.extend([capsule.get("taskId"), capsule.get("taskRef")])
    for candidate in candidates:
        ref = _normalize_spec_task_ref(candidate)
        if ref:
            return ref
    return ""


def _filter_spec_task_briefs_by_refs(
    briefs: list[dict[str, Any]],
    requested_refs: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    if not requested_refs:
        return list(briefs), []
    requested = set(requested_refs)
    selected: list[dict[str, Any]] = []
    matched: list[str] = []
    for brief in briefs:
        ref = _spec_task_ref_from_brief(brief)
        if ref in requested:
            selected.append(brief)
            if ref not in matched:
                matched.append(ref)
    return selected, matched


def _managed_research_context_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Carry compact Research truth into a later runtime route.

    A Research evidence gap blocks the unsupported claim, not necessarily a
    reversible implementation.  Keeping this projection in the route tool
    makes the handoff lossless even when a model forgets to copy the fields
    into an Engineering/Creative brief.  It contains no raw tool payloads or
    credentials.
    """

    route_context = dict((state or {}).get("current_route_context") or {}) if isinstance(state, dict) else {}
    raw_handoffs = list(route_context.get("effectiveHandoffRefs") or route_context.get("handoffRefs") or [])
    ready_ids: list[str] = []
    research_refs: list[str] = []
    limitations: list[str] = []
    gaps_by_id: dict[str, dict[str, Any]] = {}

    def add_unique(target: list[str], value: Any, limit: int = 24) -> None:
        text = str(value or "").strip()
        if text and text not in target and len(target) < limit:
            target.append(text)

    for raw in raw_handoffs:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        if "research" not in kind:
            continue
        for ref in list(raw.get("researchRefs") or raw.get("proofRefs") or []):
            add_unique(research_refs, ref, limit=12)
        for result in list(raw.get("taskBriefResults") or []):
            if not isinstance(result, dict):
                continue
            brief_id = str(result.get("taskBriefId") or result.get("taskId") or "").strip()
            if not brief_id:
                continue
            status = str(result.get("status") or "degraded").strip().lower()
            if status in {"ready", "completed", "success", "ok"}:
                add_unique(ready_ids, brief_id)
                add_unique(research_refs, result.get("researchRef"), limit=12)
                gaps_by_id.pop(brief_id, None)
                continue
            gap = {
                "taskBriefId": brief_id,
                "status": "unverified",
                "blocksClaim": True,
                "blocksDownstream": bool(result.get("blocksDownstream", False)),
                "limitations": [str(item)[:500] for item in list(result.get("limitations") or [])[:6]],
                "evidenceStatusReasons": [
                    str(item)[:160] for item in list(result.get("evidenceStatusReasons") or [])[:6]
                ],
            }
            gaps_by_id[brief_id] = gap
            for item in gap["limitations"]:
                add_unique(limitations, item, limit=8)
        for brief_id in list(raw.get("coveredTaskBriefIds") or []):
            normalized = str(brief_id or "").strip()
            if normalized:
                add_unique(ready_ids, normalized)
                gaps_by_id.pop(normalized, None)
        for brief_id in list(raw.get("missingTaskBriefIds") or []):
            normalized = str(brief_id or "").strip()
            if normalized and normalized not in gaps_by_id:
                gaps_by_id[normalized] = {
                    "taskBriefId": normalized,
                    "status": "unverified",
                    "blocksClaim": True,
                    "blocksDownstream": False,
                    "limitations": [],
                    "evidenceStatusReasons": ["missing_task_brief_evidence"],
                }
        for gap in list(raw.get("evidenceGaps") or []):
            if not isinstance(gap, dict):
                continue
            brief_id = str(gap.get("taskBriefId") or gap.get("taskId") or "").strip()
            if brief_id:
                if str(gap.get("status") or "").strip().lower() in {"ready", "completed", "success", "ok"}:
                    gaps_by_id.pop(brief_id, None)
                    add_unique(ready_ids, brief_id)
                    continue
                gaps_by_id[brief_id] = {
                    "taskBriefId": brief_id,
                    "status": "unverified",
                    "blocksClaim": bool(gap.get("blocksClaim", True)),
                    "blocksDownstream": bool(gap.get("blocksDownstream", False)),
                    "limitations": [str(item)[:500] for item in list(gap.get("limitations") or [])[:6]],
                    "evidenceStatusReasons": [
                        str(item)[:160] for item in list(gap.get("evidenceStatusReasons") or [])[:6]
                    ],
                }
        for item in list(raw.get("limitations") or []):
            add_unique(limitations, item, limit=8)

    gaps = list(gaps_by_id.values())[:24]
    if not ready_ids and not gaps and not research_refs:
        return {}
    return {
        "source": "managed_research_handoff",
        "readyTaskBriefIds": ready_ids[:24],
        "researchRefs": research_refs[:12],
        "evidenceGaps": gaps,
        "limitations": limitations[:8],
        "downstreamAllowed": bool(ready_ids) and not any(
            bool(item.get("blocksDownstream")) for item in gaps
        ),
        "requiresLocalValidation": bool(ready_ids),
        "neverClaimUnverifiedFacts": True,
    }


def _infer_route_kind_from_payload(payload: dict[str, Any], *fallbacks: Any) -> str:
    candidates: list[str] = []
    for key in ("kind", "runtimeKind", "runtime_kind", "runtime", "capability", "routeIntent", "route_intent", "tool"):
        value = payload.get(key)
        if value is not None:
            candidates.append(str(value))
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    for key in ("kind", "runtimeKind", "capability", "routeIntent", "blockedTool"):
        value = inputs.get(key)
        if value is not None:
            candidates.append(str(value))
    candidates.extend([str(item) for item in fallbacks if item])
    joined = " ".join(candidates).strip().lower().replace("-", "_")
    if not joined:
        return ""
    if any(token in joined for token in ("engineer", "project", "coding", "implementation", "write_native_file", "run_system_command", "install", "build", "workspace")):
        return "engineering"
    if any(token in joined for token in ("research", "search", "evidence", "web_research")):
        return "research"
    if any(token in joined for token in ("delegation", "subagent", "worker", "agent_swarm")):
        return "delegation"
    if any(token in joined for token in ("creative", "media", "asset", "image", "video", "audio")):
        return "creative_media"
    if any(token in joined for token in ("computer_use", "desktop", "browser", "screen")):
        return "computer_use"
    if "rpa" in joined or "trace" in joined:
        return "rpa"
    return _normalize_capability_kind(joined)


def _enrich_route_need_for_episode(
    need: dict[str, Any],
    *,
    kind: str,
    state: dict[str, Any] | None,
) -> dict[str, Any]:
    enriched = dict(need or {})
    enriched["kind"] = kind
    enriched.setdefault("source", "supervisor")
    enriched.setdefault("reason", str(enriched.get("reason") or "capability_route").strip() or "capability_route")
    inputs = dict(enriched.get("inputs") or {}) if isinstance(enriched.get("inputs"), dict) else {}
    spec_bundle = _approved_spec_execution_bundle(enriched, inputs, state=state)
    requested_spec_task_refs = _requested_spec_task_refs(enriched, inputs)
    spec_workspace_applied = False
    if spec_bundle:
        inputs["specExecutionBundle"] = spec_bundle
        enriched.setdefault("specId", spec_bundle.get("specId"))
        if str(spec_bundle.get("status") or "") == "ready":
            authoritative_workspace = str(spec_bundle.get("workspacePath") or "").strip()
            if authoritative_workspace:
                inputs["workspacePath"] = authoritative_workspace
                inputs.pop("workspace_path", None)
                spec_workspace_applied = True
            spec_groups = _required_runtime_access_from_spec_bundle(spec_bundle, kind)
            if spec_groups:
                existing_groups = list(enriched.get("requiredRuntimeAccess") or [])
                enriched["requiredRuntimeAccess"] = list(dict.fromkeys([*existing_groups, *spec_groups]))

    if not spec_workspace_applied:
        bound_workspace = _bound_workspace_path_from_state(state)
        if bound_workspace:
            requested_workspace = str(inputs.get("workspacePath") or inputs.get("workspace_path") or "").strip()
            if requested_workspace and not _same_workspace_path(requested_workspace, bound_workspace):
                inputs["workspaceBindingCorrection"] = {
                    "requestedWorkspacePath": requested_workspace,
                    "effectiveWorkspacePath": bound_workspace,
                    "reason": "current_session_binding_is_authoritative",
                }
            inputs["workspacePath"] = bound_workspace
            inputs.pop("workspace_path", None)

    # Preserve Research evidence lineage when the Supervisor moves to a
    # downstream execution runtime.  This is automatic context propagation,
    # not a route classifier or an authorization grant.
    if kind in {"engineering", "creative_media", "delegation", "computer_use", "rpa"}:
        research_context = _managed_research_context_from_state(state)
        if research_context:
            supplied_context = inputs.get("researchContext") if isinstance(inputs.get("researchContext"), dict) else {}
            merged_context = dict(research_context)
            if supplied_context:
                # Engine-owned gap/ref truth wins; retain only extra bounded
                # notes the Supervisor intentionally supplied.
                for key, value in supplied_context.items():
                    if key not in {"evidenceGaps", "researchRefs", "readyTaskBriefIds", "downstreamAllowed"}:
                        merged_context[key] = value
            inputs["researchContext"] = merged_context

    if kind in {"engineering", "delegation"}:
        explicit_route_tasks = _explicit_task_briefs_from_inputs(inputs)
        route_tasks = explicit_route_tasks
        task_filter_applied = False
        if (
            spec_bundle
            and str(spec_bundle.get("status") or "") == "ready"
            and (not route_tasks or all(str(task.get("taskBriefId") or "").startswith("route-") for task in route_tasks))
        ):
            route_tasks = _task_briefs_from_spec_bundle(spec_bundle, kind)
        if requested_spec_task_refs:
            original_task_count = len(route_tasks)
            selected_tasks, matched_refs = _filter_spec_task_briefs_by_refs(route_tasks, requested_spec_task_refs)
            if selected_tasks:
                route_tasks = selected_tasks
            else:
                route_tasks = []
                inputs["routeBriefQuality"] = {
                    "status": "blocked",
                    "reason": "requested_spec_task_not_found",
                    "blocking": True,
                    "message": "The requested Spec task reference is not present in the approved execution bundle.",
                    "requestedTaskBriefIds": requested_spec_task_refs,
                }
            inputs["selectedSpecTaskIds"] = matched_refs or requested_spec_task_refs
            inputs["specTaskFilter"] = {
                "requested": requested_spec_task_refs,
                "matched": matched_refs,
                "omittedTaskCount": max(0, original_task_count - len(route_tasks)),
                "reason": "explicit_task_ref",
            }
            inputs["targetCount"] = len(route_tasks)
            task_filter_applied = True
        if not bool((inputs.get("routeBriefQuality") or {}).get("blocking")):
            inputs["routeBriefQuality"] = _route_task_contract_quality(
                route_tasks,
                kind=kind,
                workspace_path=_workspace_path_from_route(inputs, state),
            )
        if task_filter_applied or not inputs.get("workerBriefs"):
            inputs["workerBriefs"] = route_tasks
        if task_filter_applied or not inputs.get("tasks"):
            inputs["tasks"] = route_tasks
        if task_filter_applied or not inputs.get("taskBriefs"):
            inputs["taskBriefs"] = route_tasks
        if kind == "engineering":
            inputs.setdefault(
                "proofExpectations",
                [
                    "Execute through Engineering Runtime.",
                    "Return touched files, commands, verification proof, and remaining risks.",
                ],
            )
    elif kind == "research":
        explicit_route_briefs = _explicit_task_briefs_from_inputs(inputs)
        route_briefs = explicit_route_briefs
        brief_query = ""
        for brief in route_briefs:
            if not isinstance(brief, dict):
                continue
            for key in ("routeQuery", "query", "question", "goal", "title"):
                value = str(brief.get(key) or "").strip()
                if value:
                    brief_query = value
                    break
            if brief_query:
                break
        query = str(inputs.get("query") or enriched.get("query") or brief_query or enriched.get("reason") or "").strip()
        if query:
            inputs.setdefault("query", query)
            inputs.setdefault("question", query)
        if not inputs.get("taskBriefs"):
            inputs["taskBriefs"] = route_briefs or [normalize_task_brief(_minimal_route_task_from_need(enriched, kind))]
        inputs.setdefault("sourcePolicy", "multi_source_evidence")
        research_blob = json.dumps(inputs.get("taskBriefs") or [], ensure_ascii=False, default=str).lower()
        if any(marker in research_blob for marker in ("full_read", "multi_source", "evidence_bundle", "claim_table", "claimtable", "sourcematrix", "source_matrix", "citations")):
            inputs.setdefault("mode", "run")

    if kind not in {"engineering", "delegation"}:
        explicit_tasks = _explicit_task_briefs_from_inputs(inputs)
        if explicit_tasks:
            inputs["routeBriefQuality"] = _route_task_contract_quality(
                explicit_tasks,
                kind=kind,
                workspace_path=_workspace_path_from_route(inputs, state),
            )

    enriched["inputs"] = inputs
    return enriched


_RUNTIME_LIST_ROUTE_INTENT_MARKERS = (
    "episode",
    "route",
    "wait_episode",
    "queued",
    "queue",
    "handoff",
    "plan_only",
    "work_plan",
    "dispatch",
    "degraded",
    "runtime path",
    "创建 episode",
    "创建运行时",
    "进入运行时",
    "运行时路径",
    "路由",
    "入队",
    "等待",
    "回流",
    "交接",
    "派发",
    "委派",
    "降级",
)


def _runtime_list_request_should_route(
    *,
    need: Any,
    runtime_kind: Optional[str],
    tool_group: Optional[str],
    reason: Optional[str],
    detail_level: str,
) -> bool:
    """Correct list calls that are clearly asking for episode routing.

    Some models use runtime_broker(mode="list") while their arguments say they
    want to create/wait for an episode. Catalog/detail list calls must remain
    harmless discovery, so this only triggers for summary-level list requests
    with explicit route/episode intent.
    """

    normalized_detail = str(detail_level or "summary").strip().lower()
    if normalized_detail in {"catalog", "detail", "full"}:
        return False
    route_kind = _infer_route_kind_from_payload(
        need if isinstance(need, dict) else {},
        runtime_kind,
        tool_group,
        reason,
    )
    if route_kind not in _RUNTIME_ROUTE_DEFAULT_GROUPS:
        return False
    if need:
        return True
    probe = " ".join(
        str(item or "")
        for item in (
            runtime_kind,
            tool_group,
            reason,
        )
    ).strip().lower()
    if not probe:
        return False
    return any(marker in probe for marker in _RUNTIME_LIST_ROUTE_INTENT_MARKERS)


def _append_runtime_episode(
    route_context: dict[str, Any],
    *,
    need: dict[str, Any],
    kind: str,
    groups: list[dict[str, Any]],
    allow_direct_fallback: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    route_context = dict(route_context or {})
    runtime_context = get_runtime_context()
    session_id = str(
        runtime_context.get("session_id")
        or runtime_context.get("sessionId")
        or route_context.get("session_id")
        or route_context.get("sessionId")
        or ""
    ).strip() or None
    run_id = str(
        runtime_context.get("run_id")
        or runtime_context.get("runId")
        or route_context.get("run_id")
        or route_context.get("runId")
        or ""
    ).strip() or None
    root_run_id = str(
        runtime_context.get("root_run_id")
        or runtime_context.get("rootRunId")
        or route_context.get("root_run_id")
        or route_context.get("rootRunId")
        or run_id
        or ""
    ).strip() or None
    workspace_path = str(
        runtime_context.get("workspace_path")
        or runtime_context.get("workspacePath")
        or route_context.get("workspace_path")
        or route_context.get("workspacePath")
        or ""
    ).strip() or None
    managed_engineering_context = {
        key: runtime_context.get(key)
        for key in (
            "workspace_path",
            "original_workspace_path",
            "originalWorkspacePath",
            "repository_root",
            "repositoryRoot",
            "worktree_root",
            "worktreeRoot",
            "worktree_id",
            "worktreeId",
            "sandbox_lease_id",
            "sandboxLeaseId",
            "sandbox_policy",
            "sandbox_policy_digest",
            "sandbox_policy_file",
            "sandbox_capabilities",
            "managed_engineering_execution",
        )
        if runtime_context.get(key) not in (None, "")
    }
    bound_need = dict(need or {})
    if session_id:
        bound_need.setdefault("sessionId", session_id)
        bound_need.setdefault("session_id", session_id)
    if run_id:
        bound_need.setdefault("runId", run_id)
        bound_need.setdefault("run_id", run_id)
    if root_run_id:
        bound_need.setdefault("rootRunId", root_run_id)
    inputs = dict(bound_need.get("inputs") or {}) if isinstance(bound_need.get("inputs"), dict) else {}
    if workspace_path:
        bound_need.setdefault("workspacePath", workspace_path)
        bound_need.setdefault("workspace_path", workspace_path)
        inputs.setdefault("workspacePath", workspace_path)
        inputs.setdefault("workspace_path", workspace_path)
    if managed_engineering_context.get("managed_engineering_execution"):
        original_workspace_path = str(
            managed_engineering_context.get("original_workspace_path")
            or managed_engineering_context.get("originalWorkspacePath")
            or ""
        ).strip()
        parent_worktree_id = str(
            managed_engineering_context.get("worktree_id")
            or managed_engineering_context.get("worktreeId")
            or ""
        ).strip()
        inputs.setdefault("engineeringWorkspace", managed_engineering_context)
        if original_workspace_path:
            inputs.setdefault("originalWorkspacePath", original_workspace_path)
            inputs.setdefault("original_workspace_path", original_workspace_path)
        if parent_worktree_id:
            inputs.setdefault("parentWorktreeId", parent_worktree_id)
            inputs.setdefault("parent_worktree_id", parent_worktree_id)
    bound_need["inputs"] = inputs
    episode = build_runtime_episode(
        need=bound_need,
        kind=kind,
        state="queued",
        required_runtime_access=[str((item or {}).get("group") or item) for item in groups],
        continuation_target=str(bound_need.get("continuationTarget") or "runtime_episode_runner"),
        extra={"allowDirectFallback": bool(allow_direct_fallback)},
    )
    persisted = enqueue_runtime_episode(episode, session_id=session_id, run_id=run_id, priority=int(need.get("priority") or 0))
    merged_episode = {**episode, **{k: v for k, v in persisted.items() if k in {"session_id", "sessionId", "run_id", "runId", "state", "lastHeartbeatAt"}}}
    if session_id:
        merged_episode.setdefault("sessionId", session_id)
        merged_episode.setdefault("session_id", session_id)
    if run_id:
        merged_episode.setdefault("runId", run_id)
        merged_episode.setdefault("run_id", run_id)
    if root_run_id:
        merged_episode.setdefault("rootRunId", root_run_id)
    return upsert_runtime_episode(route_context, merged_episode), merged_episode


def _emit_runtime_episode_event(topic: str, payload: dict[str, Any]) -> None:
    emit_runtime_episode_event(topic, payload, source={"runtime": "supervisor", "tool": "runtime_broker"})


@tool(args_schema=RuntimeBrokerArgs)
def runtime_broker(
    mode: Annotated[
        str,
        "Operation. Use route for execution, list for the compact catalog, and grant/revoke only for explicit run-scoped tool groups.",
    ] = "list",
    runtime_kind: Annotated[
        Optional[str],
        "Legacy/list/grant hint. For mode=route use routeKind.",
    ] = None,
    tool_group: Annotated[Optional[str], "Single run-scoped tool group for mode=grant/revoke."] = None,
    tool_groups: Annotated[Optional[list[str]], "Array of run-scoped tool groups for mode=grant/revoke."] = None,
    reason: Annotated[
        Optional[str],
        "Grant/revoke audit reason. For mode=route use routeReason.",
    ] = None,
    detail_level: Annotated[str, "summary by default; catalog/detail/full only when diagnostics are needed."] = "summary",
    routeKind: Annotated[
        Optional[str],
        "For mode=route: research, engineering, creative_media, computer_use, rpa, or delegation.",
    ] = None,
    routeReason: Annotated[Optional[str], "For mode=route: one short routing reason."] = None,
    workspacePath: Annotated[Optional[str], "Current bound workspace root; normally omit it."] = None,
    researchBriefIds: Annotated[
        Optional[list[str]],
        "Research only: complete ordered stable-ID list. Enumerate every known domain before optional detail.",
    ] = None,
    researchBriefGoals: Annotated[
        Optional[list[str]],
        "Research only: matching short goals in the same order and count as researchBriefIds.",
    ] = None,
    researchBriefContexts: Annotated[
        Optional[list[str]],
        "Research only: optional matching compact contexts; omit unless needed.",
    ] = None,
    taskBriefs: Annotated[
        Optional[list[dict[str, Any]]],
        "Non-Research only: typed execution briefs with bounded write/proof contracts where required.",
    ] = None,
    proofExpectations: Annotated[
        Optional[list[str]],
        "Compact evidence outcomes the terminal handoff must return.",
    ] = None,
    need: Annotated[
        RuntimeRouteNeed | None,
        "Deprecated hidden read-compatible route envelope.",
    ] = None,
    allow_direct_fallback: Annotated[
        bool,
        "Internal compatibility flag. Keep false for ordinary Supervisor routing.",
    ] = False,
    episode_id: Annotated[
        Optional[str],
        "Required only for mode=resume. Must identify the waiting_input episode in the current session.",
    ] = None,
    continuation_request_id: Annotated[
        Optional[str],
        "Required only for mode=resume. Must exactly match the latest waiting continuationRequest.requestId.",
    ] = None,
    continuation_inputs: Annotated[
        Optional[dict[str, Any]],
        "Required only for mode=resume. Exact user/Supervisor answers keyed by requiredInputs.id.",
    ] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """L3 managed runtime entry: route specialist lifecycle/recovery/proof and receive a typed handoff; never use it as a web reader or polling API.

    `mode='route'` uses root routeKind/routeReason fields. Research submits parallel primitive arrays
    researchBriefIds/researchBriefGoals; other runtimes submit taskBriefs. The Engine restores and strictly validates
    the canonical internal inputs envelope. Follow the Runtime capability
    registry's single Research ladder rather than inferring a route from visible tools.

    Research episode: use it for several independent fact domains, managed progress/recovery, or evidence that must cross
    into another workflow. The initial parallel arrays contain every currently known domain as stable ID + short goal;
    the Engine zips them into read-only internal task briefs. An owned missing brief gets one bounded managed repair; never
    downgrade it to direct web/research calls. Consume the terminal handoff's answers, sources, limitations, and gaps.

    Engineering episode: each brief is one coherent executable/acceptable unit. Express ordering with dependencies.
    Every write brief declares writeRequired=true, an exhaustive bounded writeSet including command side effects,
    final expectedArtifacts covered by that writeSet, expectedOutputs, and acceptanceContract. Repair only an exact
    reported contract or execution gap once; delegation is not an alternate spelling for an Engineering episode.

    Research shape: `{"mode":"route","routeKind":"research","routeReason":"verify known domains","researchBriefIds":["domain-a","domain-b"],"researchBriefGoals":["verify A","verify B"]}`.
    Engineering shape: `{"mode":"route","routeKind":"engineering","routeReason":"implement and verify","taskBriefs":[{"taskBriefId":"implementation","goal":"implement the bounded change","writeRequired":true,"writeSet":["src/feature.py"],"expectedArtifacts":["src/feature.py"],"expectedOutputs":["working implementation"],"acceptanceContract":["the requirement is implemented"]},{"taskBriefId":"verification","goal":"persist proof","writeRequired":true,"writeSet":["reports/verification.json"],"expectedArtifacts":["reports/verification.json"],"expectedOutputs":["verification report"],"acceptanceContract":["checks pass and the report records them"],"dependencies":["implementation"]}]}`.

    New Research calls use researchBriefIds + researchBriefGoals; other routes use taskBriefs. Preserve JSON array/object types. Use `list` only for a compact catalog and `grant`
    only for explicit run-scoped facade access. A successful route automatically waits for the typed handoff: never call
    repeated observe/status/wait_episode, and never claim completion from a queued episode or incomplete proof.
    In user-facing text say 深度调研、编程模式、多媒体创作、桌面操作、自动流程 or 子代理协作, not this tool name.
    """
    normalized_mode = str(mode or "list").strip().lower()
    need_payload_for_intent = _route_need_from_public_transport(
        need,
        route_kind=routeKind,
        route_reason=routeReason,
        workspace_path=workspacePath,
        research_brief_ids=researchBriefIds,
        research_brief_goals=researchBriefGoals,
        research_brief_contexts=researchBriefContexts,
        task_briefs=taskBriefs,
        proof_expectations=proofExpectations,
    )
    route_context = dict((state or {}).get("current_route_context") or {})
    if normalized_mode == "resume":
        requested_episode_id = str(episode_id or "").strip()
        requested_continuation_id = str(continuation_request_id or "").strip()
        supplied_inputs = dict(continuation_inputs or {}) if isinstance(continuation_inputs, dict) else {}
        current_session_id = str(
            (state or {}).get("session_id")
            or (state or {}).get("sessionId")
            or route_context.get("session_id")
            or route_context.get("sessionId")
            or ""
        ).strip()
        episode = db.get_runtime_episode(requested_episode_id) if requested_episode_id else None
        episode_session_id = str(
            (episode or {}).get("session_id") or (episode or {}).get("sessionId") or ""
        ).strip()
        episode_state = str((episode or {}).get("state") or "").strip().lower()
        error = ""
        summary = ""
        if not requested_episode_id or not requested_continuation_id or not supplied_inputs:
            error = "runtime_resume_inputs_required"
            summary = "mode=resume requires episode_id, continuation_request_id, and non-empty continuation_inputs."
        elif not episode:
            error = "runtime_episode_not_found"
            summary = "The requested runtime episode does not exist."
        elif not current_session_id or not episode_session_id or current_session_id != episode_session_id:
            error = "runtime_episode_session_mismatch"
            summary = "A runtime episode can only be resumed from its owning session."
        elif episode_state != "waiting_input":
            error = "runtime_episode_not_waiting_input"
            summary = f"The runtime episode is in state '{episode_state or 'unknown'}', not waiting_input."
        handoff_rows = db.list_runtime_episode_handoffs(requested_episode_id)
        latest_handoff_row = handoff_rows[-1] if handoff_rows else {}
        previous_handoff = dict(
            latest_handoff_row.get("payload")
            or latest_handoff_row.get("handoff")
            or latest_handoff_row
            or {}
        )
        normalized_inputs: dict[str, Any] = {}
        continuation_request: dict[str, Any] = {}
        if not error:
            raw_request = previous_handoff.get("continuationRequest")
            if not isinstance(raw_request, dict):
                error = "runtime_continuation_contract_missing"
                summary = "The latest waiting handoff does not contain a typed continuation request."
            else:
                try:
                    continuation_request = normalize_runtime_continuation_request(raw_request)
                    if requested_continuation_id != str(continuation_request.get("requestId") or ""):
                        raise RuntimeContinuationContractError(
                            "runtime_continuation_request_mismatch",
                            "continuation_request_id does not match the latest waiting request.",
                        )
                    request_source = dict(continuation_request.get("source") or {})
                    source_episode_id = str(request_source.get("runtimeEpisodeId") or "").strip()
                    source_session_id = str(request_source.get("sessionId") or "").strip()
                    if source_episode_id != requested_episode_id or source_session_id != episode_session_id:
                        raise RuntimeContinuationContractError(
                            "runtime_continuation_lineage_mismatch",
                            "The continuation request does not belong to this runtime episode and session.",
                        )
                    normalized_inputs = validate_runtime_continuation_answers(
                        continuation_request,
                        supplied_inputs,
                    )
                except RuntimeContinuationContractError as exc:
                    error = exc.code
                    summary = str(exc)
        if error:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=summary,
                                error=error,
                                detail_level=detail_level,
                                next_action="Use the latest typed continuation request and answer exactly its requiredInputs; do not create a replacement route.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        resumed = db.resume_runtime_episode(
            requested_episode_id,
            resume_token={
                "resumedFrom": "supervisor_input",
                "continuationRequestId": requested_continuation_id,
                "continuationInputs": normalized_inputs,
                "previousHandoffRef": str(
                    previous_handoff.get("handoffRefId") or previous_handoff.get("handoffId") or ""
                ),
                "previousHandoff": previous_handoff,
            },
        ) or {**dict(episode or {}), "state": "queued"}
        updated_context = upsert_runtime_episode(route_context, resumed)
        _emit_runtime_episode_event(
            "runtime.episode.resumed",
            {
                "episode": resumed,
                "resumeSource": "supervisor_input",
                "continuationRequestId": requested_continuation_id,
                "continuationInputKeys": sorted(str(key) for key in normalized_inputs),
            },
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime episode resumed with the requested inputs.",
                            detail_level=detail_level,
                            episode=resumed,
                            next_action="The graph owns waiting for the resumed episode; do not poll or route a replacement runtime.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_resume",
                    "dispatched": True,
                    "blocked": False,
                    "reason": "runtime_episode_resumed",
                    "episodeId": requested_episode_id,
                    "episodeKind": str(resumed.get("kind") or ""),
                    "episodeCount": 1,
                    "nextAction": "wait_episode",
                },
            },
        )
    if normalized_mode == "list" and _runtime_list_request_should_route(
        need=need_payload_for_intent,
        runtime_kind=runtime_kind,
        tool_group=tool_group,
        reason=reason,
        detail_level=detail_level,
    ):
        normalized_mode = "route"

    if normalized_mode == "list":
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime tool groups available for run-scoped grant.",
                            groups=runtime_tool_groups_catalog(),
                            grants=[
                                {"group": group, "runtimeKind": group.split(".", 1)[0]}
                                for group in runtime_access_from_route_context(route_context)
                            ],
                            detail_level=detail_level,
                            next_action=(
                                "For execution, call runtime_broker(mode='route') with routeKind, routeReason, and "
                                "researchBriefIds/researchBriefGoals or taskBriefs. Use grant only for explicit tool-group access."
                            ),
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
            },
        )

    if normalized_mode == "wait_episode":
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="Manual runtime polling is forbidden; the graph owns episode waiting and handoff resumption.",
                                error="manual_runtime_polling_forbidden",
                                detail_level=detail_level,
                                next_action="Do not call another wait/status tool. Continue only after the graph injects the typed handoff.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )

    if normalized_mode == "route":
        if not isinstance(need_payload_for_intent, dict):
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=route) requires the typed route contract.",
                                error="typed_need_required",
                                detail_level=detail_level,
                                next_action=(
                                    "Call route once with routeKind, routeReason, and taskBriefs. Write-capable tasks must include "
                                    "taskBriefId, goal, writeSet, expectedOutputs, and acceptance."
                                ),
                                parameter_guidance=runtime_route_parameter_guidance(routeKind or runtime_kind or "engineering"),
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                    "runtime_dispatch_status": {
                        "mode": "runtime_broker_route",
                        "dispatched": False,
                        "blocked": True,
                        "reason": "typed_need_required",
                        "episodeCount": 0,
                        "nextAction": "repair_task_contract",
                    },
                },
            )
        try:
            typed_need = RuntimeRouteNeed.model_validate(need_payload_for_intent)
        except ValidationError as exc:
            validation_errors = [
                {
                    "field": _public_route_validation_field(error.get("loc")),
                    "type": str(error.get("type") or "invalid"),
                }
                for error in exc.errors(include_url=False, include_context=False, include_input=False)[:8]
            ]
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=route) rejected an invalid typed need contract.",
                                error="typed_need_invalid",
                                detail_level=detail_level,
                                next_action=(
                                    "Repair routeKind, routeReason, and researchBriefIds/researchBriefGoals or taskBriefs. "
                                    "Do not pass JSON strings or infer a write contract from prose."
                                ),
                                route_brief_quality={
                                    "status": "blocked",
                                    "reason": "typed_need_invalid",
                                    "blocking": True,
                                    "validationErrors": validation_errors,
                                },
                                parameter_guidance=runtime_route_parameter_guidance(
                                    str(need_payload_for_intent.get("kind") or runtime_kind or "engineering")
                                ),
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                    "runtime_dispatch_status": {
                        "mode": "runtime_broker_route",
                        "dispatched": False,
                        "blocked": True,
                        "reason": "typed_need_invalid",
                        "episodeCount": 0,
                        "nextAction": "repair_task_contract",
                    },
                },
            )
        need_payload = typed_need.model_dump(exclude_none=True)
        route_kind = _normalize_capability_kind(need_payload.get("kind"))
        if route_kind == "research":
            # Parallel primitive arrays are the provider-safe public transport.
            # Persist and execute only the expanded internal taskBriefs contract so every
            # downstream runtime sees one stable shape.
            normalized_inputs = dict(need_payload.get("inputs") or {})
            normalized_inputs.pop("researchBriefs", None)
            normalized_inputs.pop("researchBriefContexts", None)
            need_payload["inputs"] = normalized_inputs
        if not route_kind:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=route) requires routeKind.",
                                error="missing_capability_kind",
                                next_action=(
                                    "Call route with routeKind, routeReason, and researchBriefIds/researchBriefGoals or taskBriefs."
                                ),
                                parameter_guidance=runtime_route_parameter_guidance(routeKind or runtime_kind or "engineering"),
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        if not runtime_kind_available(route_kind):
            pack_id = ""
            pack_name = ""
            try:
                from core.runtime.feature_packs import FEATURE_PACK_BY_ID, RUNTIME_FAMILY_TO_FEATURE_PACK

                pack_id = RUNTIME_FAMILY_TO_FEATURE_PACK.get(route_kind, "")
                pack_name = FEATURE_PACK_BY_ID[pack_id].product_name if pack_id else ""
            except Exception:
                pass
            summary = (
                f"{route_kind} runtime requires feature pack {pack_name or pack_id} before routing."
                if pack_id
                else f"{route_kind} runtime is not installed."
            )
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=summary,
                                error="runtime_feature_pack_required",
                                detail_level=detail_level,
                                detail_ref=f"runtimeRegistry.featurePacks.{pack_id}" if pack_id else "runtimeRegistry.featurePacks",
                                next_action="Open Admin feature packs, install the required pack, then restart Engine if prompted.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                    "runtime_dispatch_status": {
                        "mode": "runtime_broker_route",
                        "dispatched": False,
                        "blocked": True,
                        "reason": "runtime_feature_pack_required",
                        "episodeKind": route_kind,
                        "episodeCount": 0,
                        "requiredFeaturePackId": pack_id or None,
                        "nextAction": "install_feature_pack",
                    },
                },
            )
        need_payload = _enrich_route_need_for_episode(need_payload, kind=route_kind, state=state)
        route_inputs = dict(need_payload.get("inputs") or {}) if isinstance(need_payload.get("inputs"), dict) else {}
        route_brief_quality = route_inputs.get("routeBriefQuality") if isinstance(route_inputs.get("routeBriefQuality"), dict) else {}
        if bool(route_brief_quality.get("blocking")):
            blocking_reason = str(route_brief_quality.get("reason") or "task_brief_required").strip()
            public_error = blocking_reason
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=(
                                    str(route_brief_quality.get("message") or "").strip()
                                    or (
                                        f"{route_kind} runtime route needs a concrete task brief before it can queue an episode. "
                                        "Only the current Supervisor may define that execution contract."
                                    )
                                ),
                                error=public_error,
                                detail_level=detail_level,
                                next_action=(
                                    "Repair only the exact task fields or declared artifacts shown in routeBriefQuality, "
                                    "then retry the same runtime route once. Do not switch runtime families to bypass "
                                    "a contract error; read-only tasks must explicitly set readOnly=true."
                                ),
                                route_brief_quality=route_brief_quality,
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                    "runtime_dispatch_status": {
                        "mode": "runtime_broker_route",
                        "dispatched": False,
                        "blocked": True,
                        "reason": blocking_reason,
                        "episodeKind": route_kind,
                        "episodeCount": 0,
                        "nextAction": "repair_task_contract",
                    },
                },
            )
        research_repair_state: dict[str, Any] | None = None
        if route_kind == "engineering":
            engineering_tasks = _explicit_task_briefs_from_inputs(route_inputs)
            repair_state = _engineering_route_retry_state(
                tasks=engineering_tasks,
                route_context=route_context,
                state=state,
            )
            if bool(repair_state.get("exhausted")):
                return Command(
                    goto="supervisor",
                    update={
                        "messages": [
                            ToolMessage(
                                content=_runtime_broker_payload(
                                    mode=normalized_mode,
                                    ok=False,
                                    summary=(
                                        "The same Engineering write scope already used its initial attempt and one "
                                        "bounded repair; another episode was not queued."
                                    ),
                                    error="engineering_retry_exhausted",
                                    detail_level=detail_level,
                                    route_brief_quality={
                                        "status": "blocked",
                                        "reason": "engineering_retry_exhausted",
                                        "blocking": True,
                                        **repair_state,
                                    },
                                    next_action=(
                                        "Report the compact blocker and retained evidence. Only a materially different "
                                        "user-authorized scope may create a new Engineering contract in this run."
                                    ),
                                ),
                                tool_call_id=tool_call_id,
                            )
                        ],
                        "current_route_context": route_context,
                        "runtime_dispatch_status": {
                            "mode": "runtime_broker_route",
                            "dispatched": False,
                            "blocked": True,
                            "reason": "engineering_retry_exhausted",
                            "episodeKind": route_kind,
                            "episodeCount": 0,
                            "nextAction": "report_runtime_blocker",
                        },
                    },
                )
            if int(repair_state.get("priorFailedAttempts") or 0) == 1:
                route_inputs = dict(route_inputs)
                route_inputs["engineeringRepair"] = {
                    **repair_state,
                    "finalRepairAttempt": True,
                }
                need_payload = {**need_payload, "inputs": route_inputs}
        elif route_kind == "research":
            research_repair_state = _research_route_retry_state(
                tasks=_explicit_task_briefs_from_inputs(route_inputs),
                route_context=route_context,
                state=state,
            )
            if list(research_repair_state.get("inFlightEpisodeIds") or []):
                return Command(
                    goto="supervisor",
                    update={
                        "messages": [
                            ToolMessage(
                                content=_runtime_broker_payload(
                                    mode=normalized_mode,
                                    ok=False,
                                    summary="The same managed Research branch is already running; no duplicate episode was queued.",
                                    error="research_episode_in_flight",
                                    detail_level=detail_level,
                                    route_brief_quality={
                                        "status": "blocked",
                                        "reason": "research_episode_in_flight",
                                        "blocking": True,
                                        **research_repair_state,
                                    },
                                    next_action="Wait for the graph-owned typed handoff. Do not poll or replace it with direct web calls.",
                                ),
                                tool_call_id=tool_call_id,
                            )
                        ],
                        "current_route_context": route_context,
                        "runtime_dispatch_status": {
                            "mode": "runtime_broker_route",
                            "dispatched": False,
                            "blocked": True,
                            "reason": "research_episode_in_flight",
                            "episodeKind": route_kind,
                            "episodeCount": 0,
                            "nextAction": "wait_episode",
                        },
                    },
                )
            if int(research_repair_state.get("priorAttempts") or 0) == 1:
                route_inputs = dict(route_inputs)
                route_inputs["researchRepair"] = {
                    **research_repair_state,
                    "finalRepairAttempt": True,
                }
                need_payload = {**need_payload, "inputs": route_inputs}
        reused_handoff = _current_governed_handoff_reuse(
            state=state,
            need=need_payload,
            route_kind=route_kind,
        )
        if reused_handoff:
            updated_context = dict(route_context)
            updated_context["runtimeHandoffReuse"] = {
                "kind": route_kind,
                "handoffRefId": reused_handoff.get("handoffRefId"),
                "producerEpisodeId": reused_handoff.get("producerEpisodeId"),
                "reason": "same_run_read_only_evidence_reuse",
            }
            _emit_runtime_episode_event(
                "runtime.handoff.reused",
                {
                    "runtimeKind": route_kind,
                    "handoffRefId": reused_handoff.get("handoffRefId"),
                    "producerEpisodeId": reused_handoff.get("producerEpisodeId"),
                    "reason": "same_run_read_only_evidence_reuse",
                },
            )
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=True,
                                summary=(
                                    "The current governed handoff already contains complete evidence for the "
                                    "explicitly referenced read-only checks. No duplicate runtime episode was created."
                                ),
                                detail_level=detail_level,
                                changed=[],
                                detail_ref=str(reused_handoff.get("handoffRefId") or "") or None,
                                route_brief_quality={
                                    "status": "reused",
                                    "reason": "current_governed_handoff_evidence",
                                    "blocking": False,
                                },
                                next_action=(
                                    "Use the existing typed handoff and record the Supervisor acceptance decision. "
                                    "Route again only after a new user instruction or when evidence is missing or contradictory."
                                ),
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": updated_context,
                },
            )
        if route_kind == "research" and bool((research_repair_state or {}).get("exhausted")):
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary=(
                                    "The same Research branch already used its initial attempt and one bounded repair; "
                                    "another episode was not queued."
                                ),
                                error="research_retry_exhausted",
                                detail_level=detail_level,
                                route_brief_quality={
                                    "status": "blocked",
                                    "reason": "research_retry_exhausted",
                                    "blocking": True,
                                    **dict(research_repair_state or {}),
                                },
                                next_action=(
                                    "Carry the typed evidence gaps into a locally verifiable downstream task when safe, "
                                    "or report the compact blocker. Do not create an ad-hoc web-call substitute."
                                ),
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                    "runtime_dispatch_status": {
                        "mode": "runtime_broker_route",
                        "dispatched": False,
                        "blocked": True,
                        "reason": "research_retry_exhausted",
                        "episodeKind": route_kind,
                        "episodeCount": 0,
                        "nextAction": "carry_evidence_gaps_or_report_blocker",
                    },
                },
            )
        requested_groups = _capability_route_groups(
            need=need_payload,
            runtime_kind=runtime_kind or route_kind,
            tool_group=tool_group,
            tool_groups=tool_groups,
        )
        updated_context = route_context
        grants: list[dict[str, Any]] = []
        rejected: list[str] = []
        if requested_groups:
            updated_context, grants, rejected = grant_runtime_tool_groups(
                route_context,
                requested_groups,
                reason=str(reason or need_payload.get("reason") or "capability_route").strip(),
            )
        updated_context, episode = _append_runtime_episode(
            updated_context,
            need=need_payload,
            kind=route_kind,
            groups=grants,
            allow_direct_fallback=allow_direct_fallback,
        )
        _emit_runtime_episode_event("capability.need.detected", {"episode": episode})
        _emit_runtime_episode_event("runtime.episode.queued", {"episode": episode})
        if route_kind in {"engineering", "delegation"}:
            next_action = "wait_episode"
        elif route_kind == "research":
            next_action = "wait_episode"
        elif route_kind == "creative_media":
            next_action = "wait_episode"
        elif route_kind in {"computer_use", "rpa"}:
            next_action = "wait_episode"
        else:
            next_action = "wait_episode"
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=not rejected,
                            summary=f"Routed capability need to {route_kind}.",
                            grants=grants,
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
                            detail_level=detail_level,
                            changed=grants,
                            episode=episode,
                            next_action="Runtime episode queued. The graph now owns waiting and will inject the typed handoff; do not poll.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "dispatched": True,
                    "blocked": False,
                    "reason": "runtime_episode_queued",
                    "episodeId": str(episode.get("episodeId") or ""),
                    "episodeKind": route_kind,
                    "episodeCount": 1,
                    "nextAction": "wait_episode",
                },
            },
        )

    raw_requested_groups = list(tool_groups or [])
    if tool_group:
        raw_requested_groups.append(tool_group)
    requested_groups = normalize_runtime_access(raw_requested_groups, runtime_kind=runtime_kind)

    if normalized_mode == "status":
        active_groups = runtime_access_from_route_context(route_context)
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Current run-scoped runtime tool grants.",
                            groups=runtime_tool_groups_catalog(),
                            grants=[
                                {"group": group, "runtimeKind": group.split(".", 1)[0]}
                                for group in active_groups
                            ],
                            detail_level=detail_level,
                            next_action="Use granted tools or grant/revoke a group.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": route_context,
            },
        )

    if normalized_mode == "grant":
        if not raw_requested_groups:
            return Command(
                goto="supervisor",
                update={
                    "messages": [
                        ToolMessage(
                            content=_runtime_broker_payload(
                                mode=normalized_mode,
                                ok=False,
                                summary="runtime_broker(mode=grant) requires tool_group or tool_groups.",
                                error="missing_tool_group",
                                next_action="Call list, then grant a group id.",
                            ),
                            tool_call_id=tool_call_id,
                        )
                    ],
                    "current_route_context": route_context,
                },
            )
        updated_context, grants, rejected = grant_runtime_tool_groups(
            route_context,
            raw_requested_groups,
            reason=str(reason or "").strip(),
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=not rejected,
                            summary=(
                                "Runtime tool group granted for this run. It will be visible on the next supervisor step."
                                if not rejected
                                else "Some requested runtime tool groups were not granted."
                            ),
                            grants=grants,
                            groups=runtime_tool_groups_catalog() if str(detail_level or "").strip().lower() in {"catalog", "detail", "full"} else [],
                            rejected=rejected,
                            error="unknown_tool_group" if rejected else None,
                            detail_level=detail_level,
                            changed=grants,
                            next_action="Next step can use the granted tools.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
            },
        )

    if normalized_mode == "revoke":
        updated_context, grants = revoke_runtime_tool_groups(
            route_context,
            requested_groups if requested_groups else None,
        )
        return Command(
            goto="supervisor",
            update={
                "messages": [
                    ToolMessage(
                        content=_runtime_broker_payload(
                            mode=normalized_mode,
                            ok=True,
                            summary="Runtime tool grants updated for this run.",
                            grants=grants,
                            detail_level=detail_level,
                            changed=grants,
                            next_action="Continue with the remaining grants.",
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
                "current_route_context": updated_context,
            },
        )

    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=_runtime_broker_payload(
                        mode=normalized_mode or "unknown",
                        ok=False,
                        summary=f"Unsupported runtime_broker mode: {normalized_mode}",
                        error="unsupported_mode",
                        next_action="Use one of: list, route, status, grant, revoke. Episode waiting is graph-managed.",
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
            "current_route_context": route_context,
        },
    )


runtime_broker.handle_validation_error = _runtime_broker_validation_error
__all__ = [
    "RuntimeRouteInputs",
    "RuntimeRouteNeed",
    "RuntimeRouteTaskBrief",
    "runtime_broker",
    "_append_runtime_episode",
    "_capability_route_groups",
    "_emit_runtime_episode_event",
    "_enrich_route_need_for_episode",
    "_infer_route_kind_from_payload",
    "_minimal_route_task_from_need",
    "_normalize_capability_kind",
    "_route_task_contract_quality",
    "_runtime_broker_payload",
    "_runtime_list_request_should_route",
]
