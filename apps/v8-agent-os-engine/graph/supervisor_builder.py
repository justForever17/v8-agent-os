from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langgraph.types import Command

from api.models import EngineConfig
from core.engine_config_resolver import resolve_engine_config_for_role
from core.models.factory import llm_factory
from core.models.control_plane import model_control_plane
from core.model_failover_service import model_failover_service
from core.model_ref import parse_model_ref
from core.system_tools.native import NATIVE_TOOLS
from core.agents import build_subagent_registry_snapshot
from core.model_thinking_control import normalize_reasoning_effort
from core.runtime.extensions_runtime import extensions_runtime_service
from runtimes.network_supervisor.openai_compat import build_external_langchain_tools
from core.storage import storage
from erc.capability_registry import capability_registry

from .agent_factories import build_specialist_agent_components, create_subagent_chat_model
from .supervisor_execution import route_supervisor_response
from .supervisor_routing import build_supervisor_toolset, create_robust_invoke
from .supervisor_turn import execute_supervisor_turn


@dataclass
class SupervisorRuntimeBundle:
    sup_model_name: str
    caller_kwargs: dict[str, Any]
    loaded_agents: list[dict[str, Any]]
    supervisor_base_llm: Any
    robust_invoke: Callable
    supervisor_tools: list[Any]
    agent_nodes_map: dict[str, Callable]
    resolve_agent_node: Callable[[str], Any | None]
    subagent_registry_snapshot: dict[str, Any]
    supervisor_reasoning_effort: str = "auto"


def _make_dynamic_agent_node_resolver(*, storage_manager, agent_nodes_map, build_agent_components):
    """Load a newly registered Agent into the current graph on first use."""

    def resolve(agent_id: str) -> Any | None:
        normalized_id = str(agent_id or "").strip()
        if not normalized_id:
            return None
        existing = agent_nodes_map.get(normalized_id)
        if existing:
            return existing
        fresh_agent = storage_manager.get_agent(normalized_id)
        if not isinstance(fresh_agent, dict):
            return None
        fresh_components = build_agent_components([fresh_agent])
        agent_nodes_map.update(fresh_components)
        return agent_nodes_map.get(normalized_id)

    return resolve


def _is_request_model_override(config: EngineConfig, resolved_role_model_ref: str | None) -> bool:
    request_model_ref = _request_model_target(config)
    provider_id = str(config.provider or "").strip()
    model_id = str(config.model_name or "").strip()
    role_model_ref = str(resolved_role_model_ref or "").strip()
    if not model_id or model_id == "gpt-4o" or not provider_id:
        return False
    if not request_model_ref or request_model_ref == role_model_ref:
        return False
    role_identity = parse_model_ref(role_model_ref)
    request_identity = parse_model_ref(request_model_ref)
    if role_identity and not request_identity:
        return (provider_id, model_id) != role_identity
    return True


def _request_model_target(config: EngineConfig) -> str:
    model_id = str(config.model_name or "").strip()
    provider_id = str(config.provider or "").strip()
    if not model_id:
        return ""
    record = model_control_plane.get_model_record(model_id, provider_id=provider_id)
    return str((record or {}).get("model_ref") or model_id)


def build_supervisor_runtime_bundle(
    *,
    config: EngineConfig,
    fetch_skill_instructions_tool,
    build_failure_command: Callable,
    extract_task_context: Callable,
    resolve_todos: Callable,
    sanitize_message_chain: Callable,
    sanitize_response_tool_calls: Callable,
) -> SupervisorRuntimeBundle:
    caller_kwargs: dict[str, Any] = {}
    if config.api_key:
        caller_kwargs["api_key"] = config.api_key
    if config.base_url:
        caller_kwargs["base_url"] = config.base_url
    caller_kwargs.setdefault("timeout", 180)

    sup_config = storage.get_supervisor_config()
    supervisor_resolution = resolve_engine_config_for_role("supervisor")
    role_resolution = supervisor_resolution["resolution"]
    sup_model_name = str(
        role_resolution.get("resolvedModelRef")
        or role_resolution.get("resolvedModelId")
        or ""
    )
    supervisor_binding_state = str(supervisor_resolution["resolution"].get("bindingState") or "")
    default_role_model = storage.get_role_model_id("default")
    request_model_override = _is_request_model_override(config, sup_model_name)

    if request_model_override:
        sup_model_name = _request_model_target(config)
    elif supervisor_binding_state != "explicit" and config.model_name and config.model_name != default_role_model:
        sup_model_name = _request_model_target(config)
    if not sup_model_name:
        sup_model_name = default_role_model or config.model_name

    supervisor_reasoning_effort = normalize_reasoning_effort(getattr(config, "supervisor_reasoning_effort", None))
    supervisor_model_kwargs = dict(caller_kwargs)
    if supervisor_reasoning_effort != "auto":
        supervisor_model_kwargs["_reasoning_effort"] = supervisor_reasoning_effort

    # The chat runtime projects trusted provider reasoning from model stream
    # callbacks. Keep the Supervisor streaming even though failover uses the
    # synchronous invoke facade; LangChain aggregates the final AIMessage while
    # still emitting the provider chunks needed by the Human Surface.
    supervisor_base_llm = llm_factory.create_chat_model(sup_model_name, streaming=True, **supervisor_model_kwargs)
    default_agent_model_id = sup_model_name if request_model_override else (storage.get_default_agent_model_id() or sup_model_name)
    if not default_agent_model_id:
        default_agent_llm = supervisor_base_llm
    else:
        default_agent_llm = create_subagent_chat_model(
            default_agent_model_id,
            role="subagent",
            streaming=True,
            **caller_kwargs,
        )

    all_mcp_tools = extensions_runtime_service.get_mcp_tools()
    external_tools = build_external_langchain_tools(config.external_tools)
    loaded_agents = storage.get_all_agents()
    subagent_registry_snapshot = build_subagent_registry_snapshot(
        loaded_agents,
        (sup_config.get("specialistRegistry") if isinstance(sup_config.get("specialistRegistry"), dict) else None),
    )
    filtered_native_tools = capability_registry.filter_direct_tools(NATIVE_TOOLS)

    robust_invoke = create_robust_invoke(
        sup_model_name=sup_model_name,
        llm_factory=llm_factory,
        model_control_plane=model_control_plane,
        model_failover_service=model_failover_service,
        supervisor_reasoning_effort=supervisor_reasoning_effort,
    )

    def _build_agent_components(agent_records: list[dict[str, Any]]) -> dict[str, Any]:
        return build_specialist_agent_components(
            loaded_agents=agent_records,
            all_mcp_tools=all_mcp_tools,
            filtered_native_tools=filtered_native_tools,
            default_agent_llm=default_agent_llm,
            supervisor_model_id=sup_model_name,
            robust_invoke=robust_invoke,
            build_failure_command=build_failure_command,
            extract_task_context=extract_task_context,
            resolve_todos=resolve_todos,
            sanitize_message_chain=sanitize_message_chain,
            sanitize_response_tool_calls=sanitize_response_tool_calls,
            fetch_skill_instructions=fetch_skill_instructions_tool,
        )

    agent_nodes_map = _build_agent_components(loaded_agents)

    resolve_agent_node = _make_dynamic_agent_node_resolver(
        storage_manager=storage,
        agent_nodes_map=agent_nodes_map,
        build_agent_components=_build_agent_components,
    )

    supervisor_tools = build_supervisor_toolset(
        fetch_skill_instructions_tool=fetch_skill_instructions_tool,
        filtered_native_tools=filtered_native_tools,
        external_tools=external_tools,
        all_mcp_tools=all_mcp_tools,
        supervisor_allowed_tools=sup_config.get("allowed_tools"),
        config_allowed_tools=config.allowed_tools,
    )

    return SupervisorRuntimeBundle(
        sup_model_name=sup_model_name,
        caller_kwargs=caller_kwargs,
        loaded_agents=loaded_agents,
        supervisor_base_llm=supervisor_base_llm,
        robust_invoke=robust_invoke,
        supervisor_tools=supervisor_tools,
        agent_nodes_map=agent_nodes_map,
        resolve_agent_node=resolve_agent_node,
        subagent_registry_snapshot=subagent_registry_snapshot,
        supervisor_reasoning_effort=supervisor_reasoning_effort,
    )


def build_supervisor_node(
    *,
    config: EngineConfig,
    bundle: SupervisorRuntimeBundle,
    memory_runtime,
    scope_resolution_service,
    ensure_reasoning_content,
    sanitize_message_chain: Callable,
    context_orchestrator,
    sanitize_response_tool_calls: Callable,
):
    from core.automation.hooks import hooks_manager

    def supervisor_node(state):
        current_agents = storage.get_all_agents()
        current_registry_snapshot = build_subagent_registry_snapshot(
            current_agents,
            ((storage.get_supervisor_config() or {}).get("specialistRegistry") or {}),
        )
        state_with_registry = {
            **dict(state),
            "subagent_registry_snapshot": dict(current_registry_snapshot or {}),
        }
        messages = list(state_with_registry["messages"])
        hooks_manager.execute_hook("on_supervisor_start")
        response = execute_supervisor_turn(
            state=state_with_registry,
            config=config,
            messages=messages,
            loaded_agents=current_agents,
            supervisor_tools=bundle.supervisor_tools,
            memory_runtime=memory_runtime,
            scope_resolution_service=scope_resolution_service,
            ensure_reasoning_content=ensure_reasoning_content,
            sanitize_message_chain=sanitize_message_chain,
            context_orchestrator=context_orchestrator,
            robust_invoke=bundle.robust_invoke,
            supervisor_base_llm=bundle.supervisor_base_llm,
            sup_model_name=bundle.sup_model_name,
            caller_kwargs=bundle.caller_kwargs,
            supervisor_reasoning_effort=bundle.supervisor_reasoning_effort,
            llm_factory=llm_factory,
            sanitize_response_tool_calls=sanitize_response_tool_calls,
        )
        hooks_manager.execute_hook("on_supervisor_end")
        routed = route_supervisor_response(
            response,
            existing_route_context=dict(state.get("current_route_context") or {}),
        )
        routed_update = dict(routed.update or {}) if isinstance(routed.update, dict) else {}
        state_compaction_updates = list(
            getattr(response, "_v8_state_compaction_updates", ()) or ()
        )
        if state_compaction_updates:
            routed_update["messages"] = [
                *state_compaction_updates,
                *list(routed_update.get("messages") or []),
            ]
        return Command(
            graph=routed.graph,
            goto=routed.goto,
            resume=routed.resume,
            update={
                **routed_update,
                "subagent_registry_snapshot": dict(bundle.subagent_registry_snapshot or {}),
            },
        )

    return supervisor_node
