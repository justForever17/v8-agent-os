from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from api.models import EngineConfig
from core.engine_config_resolver import resolve_engine_config_for_role
from core.models.factory import llm_factory
from core.models.control_plane import model_control_plane
from core.model_failover_service import model_failover_service
from core.system_tools.native import NATIVE_TOOLS
from core.plugin_host.tool_registry import plugin_host_tool_registry
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


def _is_request_model_override(config: EngineConfig, default_role_model: str | None) -> bool:
    return bool(
        config.model_name
        and config.model_name != "gpt-4o"
        and config.model_name != default_role_model
        and str(config.provider or "").strip().lower() not in {"", "openai"}
    )


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
    sup_model_name = str(supervisor_resolution["resolution"].get("resolvedModelId") or "")
    supervisor_binding_state = str(supervisor_resolution["resolution"].get("bindingState") or "")
    default_role_model = storage.get_role_model_id("default")
    request_model_override = _is_request_model_override(config, default_role_model)

    if request_model_override:
        sup_model_name = config.model_name
    elif supervisor_binding_state != "explicit" and config.model_name and config.model_name != default_role_model:
        sup_model_name = config.model_name
    if not sup_model_name:
        sup_model_name = default_role_model or config.model_name

    supervisor_base_llm = llm_factory.create_chat_model(sup_model_name, streaming=False, **caller_kwargs)
    default_agent_model_id = sup_model_name if request_model_override else (storage.get_default_agent_model_id() or sup_model_name)
    if not default_agent_model_id:
        default_agent_llm = supervisor_base_llm
    else:
        default_agent_llm = create_subagent_chat_model(
            default_agent_model_id,
            role="subagent",
            streaming=False,
            **caller_kwargs,
        )

    all_mcp_tools = extensions_runtime_service.get_mcp_tools()
    plugin_host_tools = plugin_host_tool_registry.build_supervisor_tools()
    external_tools = build_external_langchain_tools(config.external_tools)
    loaded_agents = storage.get_all_agents()
    filtered_native_tools = capability_registry.filter_direct_tools(NATIVE_TOOLS)

    robust_invoke = create_robust_invoke(
        sup_model_name=sup_model_name,
        llm_factory=llm_factory,
        model_control_plane=model_control_plane,
        model_failover_service=model_failover_service,
    )

    agent_nodes_map = build_specialist_agent_components(
        loaded_agents=loaded_agents,
        all_mcp_tools=all_mcp_tools,
        all_plugin_host_tools=plugin_host_tools,
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

    supervisor_tools = build_supervisor_toolset(
        fetch_skill_instructions_tool=fetch_skill_instructions_tool,
        filtered_native_tools=filtered_native_tools,
        external_tools=external_tools,
        all_mcp_tools=all_mcp_tools,
        plugin_host_tools=plugin_host_tools,
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
        messages = list(state["messages"])
        hooks_manager.execute_hook("on_supervisor_start")
        response = execute_supervisor_turn(
            state=state,
            config=config,
            messages=messages,
            loaded_agents=bundle.loaded_agents,
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
            llm_factory=llm_factory,
            sanitize_response_tool_calls=sanitize_response_tool_calls,
        )
        hooks_manager.execute_hook("on_supervisor_end")
        return route_supervisor_response(
            response,
            existing_route_context=dict(state.get("current_route_context") or {}),
        )

    return supervisor_node
