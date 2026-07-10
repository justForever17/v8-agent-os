from typing import Annotated, NotRequired, Sequence, TypedDict
import operator

from runtimes.memory.scope_resolution import scope_resolution_service
from core.models.provider_compatibility import install_provider_compatibility_patches
from core.response_normalizer import ensure_reasoning_content

from langchain_core.messages import BaseMessage

from runtimes.extensions.skills.loader import fetch_skill_instructions
from api.models import EngineConfig
from .compat import sanitize_message_chain as compat_sanitize_message_chain
from .compat import sanitize_response_tool_calls as compat_sanitize_response_tool_calls
from .tool_routing import create_routed_tool_node as tool_routing_create_routed_tool_node
from .supervisor_builder import build_supervisor_node, build_supervisor_runtime_bundle
from .supervisor_support import build_agent_runtime_failure_command, extract_task_context, resolve_todos
from .workflow_assembly import compile_supervisor_workflow
from .route_context import merge_route_context

install_provider_compatibility_patches()

from langgraph.managed import RemainingSteps

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    remaining_steps: RemainingSteps
    todos: Annotated[list, operator.add]
    delegation_contexts: Annotated[list, operator.add]
    parallel_results: Annotated[list, operator.add]
    parallel_invocations: Annotated[list, operator.add]
    pending_child_delegations: Annotated[list, operator.add]
    current_route_context: Annotated[dict, merge_route_context]
    transport: NotRequired[str]
    planner_plan: NotRequired[dict]
    planner_dispatch_status: NotRequired[dict]
    engineering_context: NotRequired[dict]
    task_shape_hint: NotRequired[dict]
    explicit_subagent_families: NotRequired[list]
    subagent_registry_snapshot: NotRequired[dict]
    context_mentions: NotRequired[list]
    context_session_refs: NotRequired[list]
    contextSessionRefs: NotRequired[list]
    session_id: NotRequired[str]
    sessionId: NotRequired[str]
    run_id: NotRequired[str]
    runId: NotRequired[str]
    workspace_path: NotRequired[str]
    workspacePath: NotRequired[str]
    workspace_id: NotRequired[str]
    workspaceId: NotRequired[str]
    resolved_scope: NotRequired[str]
    resolvedScope: NotRequired[str]

from core.context_orchestrator import context_orchestrator

_build_agent_runtime_failure_command = build_agent_runtime_failure_command


def _get_memory_runtime():
    from runtimes.memory.runtime import memory_runtime

    return memory_runtime


def create_supervisor_graph(config: EngineConfig, checkpointer=None):
    """Initializes and returns the compiled LangGraph execution environment using Multi-Agent Command routing."""
    bundle = build_supervisor_runtime_bundle(
        config=config,
        fetch_skill_instructions_tool=fetch_skill_instructions,
        build_failure_command=build_agent_runtime_failure_command,
        extract_task_context=extract_task_context,
        resolve_todos=resolve_todos,
        sanitize_message_chain=compat_sanitize_message_chain,
        sanitize_response_tool_calls=compat_sanitize_response_tool_calls,
    )

    supervisor_node = build_supervisor_node(
        config=config,
        bundle=bundle,
        memory_runtime=_get_memory_runtime(),
        scope_resolution_service=scope_resolution_service,
        ensure_reasoning_content=ensure_reasoning_content,
        sanitize_message_chain=compat_sanitize_message_chain,
        context_orchestrator=context_orchestrator,
        sanitize_response_tool_calls=compat_sanitize_response_tool_calls,
    )

    return compile_supervisor_workflow(
        agent_state_type=AgentState,
        supervisor_node=supervisor_node,
        supervisor_tools=bundle.supervisor_tools,
        agent_nodes_map=bundle.agent_nodes_map,
        create_routed_tool_node=tool_routing_create_routed_tool_node,
        checkpointer=checkpointer,
    )

