from typing import Callable

from core.computer_use_tool_surface import (
    DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES,
    SUPERVISOR_ALLOW_LOW_LEVEL_COMPUTER_USE_MARKER,
    SUPERVISOR_HIGH_LEVEL_COMPUTER_USE_TOOLS,
    SUPERVISOR_LOW_LEVEL_COMPUTER_USE_TOOLS,
    normalize_supervisor_native_allowlist as _normalize_supervisor_native_allowlist,
    select_supervisor_native_tools as _select_supervisor_native_tools,
)
from core.runtime_tool_access import all_runtime_group_tool_names, tool_ref_name


def create_robust_invoke(
    *,
    sup_model_name: str,
    llm_factory,
    model_control_plane,
    model_failover_service,
    supervisor_reasoning_effort: str = "auto",
):
    def _robust_invoke(base_llm_instance, messages, tools=None, *, role="supervisor", preferred_model_id="", build_model=None):
        import logging

        logger = logging.getLogger("v8chat.supervisor")
        resolved_config = model_control_plane.get_config()
        target_model_id = preferred_model_id or sup_model_name
        def _default_model_builder(candidate_model_id):
            kwargs = {"streaming": False, "timeout": 180, "_role": role}
            if role == "supervisor" and supervisor_reasoning_effort and supervisor_reasoning_effort != "auto":
                kwargs["_reasoning_effort"] = supervisor_reasoning_effort
            return llm_factory.create_chat_model(candidate_model_id, **kwargs)

        model_builder = build_model or _default_model_builder

        logger.info(
            "[RobustInvoke] role=%s preferred_model=%s tools=%s",
            role,
            target_model_id,
            bool(tools),
        )
        return model_failover_service.invoke_with_failover(
            config=resolved_config,
            base_llm_instance=base_llm_instance,
            messages=messages,
            tools=tools,
            role=role,
            preferred_model_id=target_model_id,
            build_model=model_builder,
        )

    return _robust_invoke


def build_supervisor_toolset(
    *,
    fetch_skill_instructions_tool,
    filtered_native_tools,
    external_tools=None,
    all_mcp_tools,
    supervisor_allowed_tools,
    config_allowed_tools,
):
    selected_native_tools = _select_supervisor_native_tools(
        filtered_native_tools=filtered_native_tools,
        supervisor_allowed_tools=supervisor_allowed_tools,
        config_allowed_tools=config_allowed_tools,
    )
    grantable_runtime_tool_names = all_runtime_group_tool_names()
    grantable_runtime_tools = [
        tool_ref
        for tool_ref in list(filtered_native_tools or [])
        if tool_ref_name(tool_ref) in grantable_runtime_tool_names
    ]
    selected_native_tools = list(selected_native_tools) + grantable_runtime_tools
    broker_tools = [
        tool_ref
        for tool_ref in selected_native_tools
        if str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip() == "delegation_broker"
    ]
    remaining_native_tools = [tool_ref for tool_ref in selected_native_tools if tool_ref not in broker_tools]
    supervisor_tools = []
    seen_tool_names: set[str] = set()
    for tool_ref in [fetch_skill_instructions_tool] + broker_tools + remaining_native_tools + list(external_tools or []):
        name = tool_ref_name(tool_ref) or str(id(tool_ref))
        if name in seen_tool_names:
            continue
        seen_tool_names.add(name)
        supervisor_tools.append(tool_ref)

    if supervisor_allowed_tools is not None:
        allowed_mcp_tools = [tool for tool in all_mcp_tools if tool.name in supervisor_allowed_tools]
        supervisor_tools.extend(allowed_mcp_tools)
    elif config_allowed_tools is not None:
        allowed_mcp_tools = [tool for tool in all_mcp_tools if tool.name in config_allowed_tools]
        supervisor_tools.extend(allowed_mcp_tools)
    else:
        supervisor_tools.extend(all_mcp_tools)

    return supervisor_tools
