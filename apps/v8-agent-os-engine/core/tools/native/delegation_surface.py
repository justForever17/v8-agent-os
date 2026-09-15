"""Model-facing manual dispatch contract; execution remains in delegation_broker."""

from typing import Annotated, Any, Literal
import json

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import ConfigDict, Field, TypeAdapter
from typing_extensions import Required

from core.tools.native.delegation import DelegationTaskInput, delegation_broker


class ManualLocalDelegationTask(DelegationTaskInput, total=False):
    targetAgentName: Required[Annotated[str, Field(min_length=1, pattern=r"\S", description="Exact registered Agent name from the visible registry or agent_broker(mode='list').")]]
    executionLaneHint: Literal["auto", "subagent"]


class ManualExternalDelegationTask(DelegationTaskInput, total=False):
    executionLaneHint: Required[Literal["external_worker"]]


def delegation_parameter_repair(invalid_fields: list[str]) -> tuple[str, list[str]]:
    """Explain the current public union without exposing Pydantic branch names."""
    local_required = TypeAdapter(ManualLocalDelegationTask).json_schema().get("required", [])
    external_required = TypeAdapter(ManualExternalDelegationTask).json_schema().get("required", [])
    fields = list(dict.fromkeys(field.replace(".ManualLocalDelegationTask.", ".").replace(".ManualExternalDelegationTask.", ".")
                                for field in invalid_fields))
    task_fields = TypeAdapter(DelegationTaskInput).json_schema().get("properties", {})
    misplaced = [field for field in fields if field in task_fields]
    example = {
        "mode": "dispatch",
        "tasks": [{"taskBriefId": "<retain task id>", "targetAgentName": "<exact registered name from agent_broker(mode='list')>",
                   "goal": "<retain authorized goal>", "expectedOutputs": ["<concrete result>"],
                   "acceptanceContract": ["<observable acceptance check>"]}],
    }
    return "\n".join([
        "Delegation parameters were rejected before execution; no episode or worker was dispatched by this call.",
        "Invalid field paths: " + ", ".join(fields),
        *(["Task fields were placed at the broker level. Put each field on the intended original task: "
           + ", ".join(f"{field} -> tasks[i].{field}" for field in misplaced)
           + ". No values have been moved or permissions granted."] if misplaced else []),
        "For a local registered Agent, tasks[i] requires: " + ", ".join(local_required) + ".",
        "Set tasks[i].targetAgentName to the exact name from the visible registry or agent_broker(mode='list'). A family or preferredAgentId does not replace targetAgentName.",
        "For an explicitly requested external worker only, tasks[i] instead requires: " + ", ".join(external_required) + "; executionLaneHint must be 'external_worker'.",
        "These are alternative task variants. Do not switch a local task to external_worker to bypass a missing name. Do not add the validation branch class names as JSON fields.",
        "Preserve the authorized goal, evidence, outputs, acceptance, read/write boundaries and task IDs; repair this call's fields without widening permissions. Omit unused optional fields, including null lane/selector values.",
        "Use only declared broker-level fields. Task constraints and execution boundaries belong on tasks[i], not beside tasks.",
        "Local shape (replace placeholders; do not copy a made-up Agent name): " + json.dumps(example, ensure_ascii=False),
    ]), fields


@tool("delegation_broker")
def supervisor_delegation_broker(
    mode: Literal["dispatch", "observe", "inspect", "steer", "cancel", "await", "resume", "review_result"] = "observe",
    tasks: Annotated[
        list[ManualLocalDelegationTask | ManualExternalDelegationTask] | None,
        "For dispatch use a flat array. Each local task requires targetAgentName, taskBriefId, goal, expectedOutputs and acceptanceContract. Omit unused optional fields; do not send null strings or taskBrief wrappers.",
    ] = None,
    family: str = "",
    target_count: int | None = None,
    allow_child_delegation: bool = False,
    child_delegation_budget: dict[str, Any] | None = None,
    write_set_partitions: list[dict[str, Any]] | None = None,
    delegation_id: str = "",
    followup: str = "",
    handoff_id: Annotated[str, "For review_result: current handoffRefId from inspect; binds the exact immutable result version."] = "",
    task_brief_id: Annotated[str, "For review_result: one taskBriefId in that handoff's results."] = "",
    decision: Literal["", "accept", "retry", "ignore"] = "",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
    state: Annotated[dict[str, Any], InjectedState] = None,
) -> Command:
    """Delegate bounded work to registered specialists or explicit external workers.

    Use the exact visible Agent name, or discover it with agent_broker(mode='list').
    This tool has no list mode. Local dispatch tasks require targetAgentName;
    external tasks require executionLaneHint='external_worker'. Never use family
    as a substitute for a local Agent name. Use the declared JSON types and omit
    unused optional fields. Each task needs its goal, inputs/evidence refs,
    expectedOutputs, acceptanceContract and read/write boundaries.

    Read-only does not mean no tools: toolPolicy.mode='default' retains the role's
    allowed tools. Set readOnly=true, writeRequired=false and writeSet=[] for a
    verifier. Provide exact saved Research refs for evidence reads. Runtime tools
    follow the Agent's binding; no extra runtime activation grants are needed.
    These execution fields belong directly on tasks[i], never directly inside
    context. Use JSON booleans and path arrays, not strings such as "true" or "".
    Context holds facts/evidence; a missing typed Capsule with shadow execution
    fields is rejected before dispatch with exact repair paths and exampleTasks.
    Review that example and explicitly resubmit without widening permissions.

    For read-only inventories, workers can use creative_media_capabilities
    (rank_models/catalog) for configured media models, computer_use_list_apps
    for applications, and browser_capabilities for the managed browser. These
    discovery tools do not generate media, launch apps or change configuration;
    no execution-group grant is needed. Preserve explicit forbiddenTools and
    allowlists, but do not translate "no generation" into a discovery ban.
    config_broker is Supervisor-only and cannot be granted to workers. Never
    assign its modes or invent config_broker_* tool names for a child; provide
    authorized evidence or assign the appropriate visible read-only discovery.

    Local dispatch returns durable episode handles and lets you continue
    independent work without overlapping write sets. inspect (or observe) reads
    current progress and control receipts. steer uses followup and is applied at
    a safe point in the original episode; cancel remains pending until its
    executor actually stops. await yields for delegation_id; runtime_broker
    await supports several episode_ids. Avoid busy polling. resume retains its
    external-worker meaning.
    When task policy permits delegation_broker, the direct worker receives its
    own schema, including mode='publish_partial',
    partial_handoff={outputKey, version, sourceVersion,
    usableFor, compactSummary, proofRefs}, under its active episode lease.
    That worker-only mode is intentionally absent from this Supervisor schema.
    Publishing a partial is not child dispatch and does not finish the worker;
    allowChildDelegation=false does not forbid this scoped progress operation.
    inspect.executionTerminal includes degraded, failed and cancelled outcomes;
    completedAt is the persisted settlement timestamp when available. A missing
    historical timestamp leaves the time unknown; use state to judge activity.
    While executionTerminal=false, a status=partial handoff is usable only after
    the Supervisor inspects its proof and explicitly chooses scoped acceptance:
    runtime_broker(mode='accept_partial', episode_id=<producer episodeId>,
    handoff_id=<current partial handoffRefId>, consumers=<needed subset of usableFor>,
    reason=<evidence basis>). This does not finish the child or authorize other
    consumers. Do not route a new episode or use review_result for a partial.
    Only after executionTerminal=true, inspect the final evidence and use
    review_result for each final result separately: delegation_id,
    handoff_id, task_brief_id, decision=accept|retry|ignore, followup=evidence basis.
    A prose ACCEPT never records a decision. Unknown evidence remains pending;
    retry records an unmet item and requires a repaired attempt before completion.
    """
    return delegation_broker.func(
        mode=mode, tasks=tasks, family=family, target_count=target_count,
        allow_child_delegation=allow_child_delegation,
        child_delegation_budget=child_delegation_budget,
        write_set_partitions=write_set_partitions,
        delegation_id=delegation_id, followup=followup,
        handoff_id=handoff_id, task_brief_id=task_brief_id, decision=decision,
        tool_call_id=tool_call_id, state=state,
    )


class SupervisorDelegationArguments(supervisor_delegation_broker.args_schema):
    """Keep signature-derived fields and injections; reject silent field loss."""

    model_config = ConfigDict(extra="forbid")


supervisor_delegation_broker.args_schema = SupervisorDelegationArguments
