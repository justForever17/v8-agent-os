"""Model-facing manual dispatch contract; execution remains in delegation_broker."""

from typing import Annotated, Any, Literal

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import Field
from typing_extensions import Required

from core.tools.native.delegation import DelegationTaskInput, delegation_broker


class ManualLocalDelegationTask(DelegationTaskInput, total=False):
    targetAgentName: Required[Annotated[str, Field(min_length=1, pattern=r"\S", description="Exact registered Agent name from the visible registry or agent_broker(mode='list').")]]
    executionLaneHint: Literal["auto", "subagent"]


class ManualExternalDelegationTask(DelegationTaskInput, total=False):
    executionLaneHint: Required[Literal["external_worker"]]


@tool("delegation_broker")
def supervisor_delegation_broker(
    mode: Literal["dispatch", "observe", "resume"] = "observe",
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

    For read-only inventories, workers can use creative_media_capabilities
    (rank_models/catalog) for configured media models, computer_use_list_apps
    for applications, and browser_capabilities for the managed browser. These
    discovery tools do not generate media, launch apps or change configuration;
    no execution-group grant is needed. Preserve explicit forbiddenTools and
    allowlists, but do not translate "no generation" into a discovery ban.
    config_broker is Supervisor-only and cannot be granted to workers. Never
    assign its modes or invent config_broker_* tool names for a child; provide
    authorized evidence or assign the appropriate visible read-only discovery.

    Local results arrive through graph handoffs; do not poll. Observe/resume are
    for an explicit external delegation_id or a terminal diagnostic read.
    The Supervisor must inspect evidence and accept/retry/ignore the result.
    """
    return delegation_broker.func(
        mode=mode, tasks=tasks, family=family, target_count=target_count,
        allow_child_delegation=allow_child_delegation,
        child_delegation_budget=child_delegation_budget,
        write_set_partitions=write_set_partitions,
        delegation_id=delegation_id, followup=followup,
        tool_call_id=tool_call_id, state=state,
    )
