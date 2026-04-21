from typing import Callable

from core.computer_use_tool_surface import (
    DEFAULT_SUPERVISOR_NATIVE_TOOL_EXCLUDES,
    SUPERVISOR_ALLOW_LOW_LEVEL_COMPUTER_USE_MARKER,
    SUPERVISOR_HIGH_LEVEL_COMPUTER_USE_TOOLS,
    SUPERVISOR_LOW_LEVEL_COMPUTER_USE_TOOLS,
    normalize_supervisor_native_allowlist as _normalize_supervisor_native_allowlist,
    select_supervisor_native_tools as _select_supervisor_native_tools,
)

def _matches_allowed_plugin_host_tool(tool, allowlist) -> bool:
    if allowlist is None:
        return True
    normalized_allowlist = {str(item).strip() for item in allowlist if str(item).strip()}
    if not normalized_allowlist:
        return False
    metadata = dict(getattr(tool, "metadata", {}) or {})
    candidates = {
        str(tool.name or "").strip(),
        str(metadata.get("canonicalName") or "").strip(),
        str(metadata.get("rawName") or "").strip(),
    }
    plugin_id = str(metadata.get("pluginId") or "").strip()
    if plugin_id:
        candidates.add(plugin_id)
        raw_name = str(metadata.get("rawName") or "").strip()
        if raw_name:
            candidates.add(f"{plugin_id}.{raw_name}")
    candidates = {item for item in candidates if item}
    return bool(candidates & normalized_allowlist)


def create_robust_invoke(
    *,
    sup_model_name: str,
    llm_factory,
    model_control_plane,
    model_failover_service,
):
    def _robust_invoke(base_llm_instance, messages, tools=None, *, role="supervisor", preferred_model_id="", build_model=None):
        import logging

        logger = logging.getLogger("v8chat.supervisor")
        resolved_config = model_control_plane.get_config()
        target_model_id = preferred_model_id or sup_model_name
        model_builder = build_model or (
            lambda candidate_model_id: llm_factory.create_chat_model(candidate_model_id, streaming=True, _role=role)
        )

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
    create_agent_tool,
    delegate_parallel_tool,
    handoff_tools,
    filtered_native_tools,
    all_mcp_tools,
    plugin_host_tools,
    supervisor_allowed_tools,
    config_allowed_tools,
):
    selected_native_tools = _select_supervisor_native_tools(
        filtered_native_tools=filtered_native_tools,
        supervisor_allowed_tools=supervisor_allowed_tools,
        config_allowed_tools=config_allowed_tools,
    )
    broker_tools = [
        tool_ref
        for tool_ref in selected_native_tools
        if str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip() == "delegation_broker"
    ]
    remaining_native_tools = [tool_ref for tool_ref in selected_native_tools if tool_ref not in broker_tools]
    supervisor_tools = [fetch_skill_instructions_tool] + broker_tools + remaining_native_tools

    if supervisor_allowed_tools is not None:
        allowed_mcp_tools = [tool for tool in all_mcp_tools if tool.name in supervisor_allowed_tools]
        supervisor_tools.extend(allowed_mcp_tools)
    elif config_allowed_tools is not None:
        allowed_mcp_tools = [tool for tool in all_mcp_tools if tool.name in config_allowed_tools]
        supervisor_tools.extend(allowed_mcp_tools)
    else:
        supervisor_tools.extend(all_mcp_tools)

    if supervisor_allowed_tools is not None:
        allowed_plugin_host_tools = [
            tool for tool in plugin_host_tools if _matches_allowed_plugin_host_tool(tool, supervisor_allowed_tools)
        ]
        supervisor_tools.extend(allowed_plugin_host_tools)
    elif config_allowed_tools is not None:
        allowed_plugin_host_tools = [
            tool for tool in plugin_host_tools if _matches_allowed_plugin_host_tool(tool, config_allowed_tools)
        ]
        supervisor_tools.extend(allowed_plugin_host_tools)
    else:
        supervisor_tools.extend(plugin_host_tools)

    return supervisor_tools
