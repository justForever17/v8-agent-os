from __future__ import annotations

from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from core.tool_surface import (
    MAX_TOOL_OUTPUT_LENGTH,
    apply_agent_visible_budget,
    apply_tool_surface_budget,
    tool_output_budget_for_request,
)

DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS = 15000


def _tool_output_budget_for_request(request: Any, tool_name: str) -> dict[str, Any]:
    return tool_output_budget_for_request(request, tool_name)


def _truncate_tool_message_content(message: ToolMessage, budget_meta: dict[str, Any] | None = None) -> ToolMessage:
    return apply_tool_surface_budget(message, budget_meta)


def _truncate_command_tool_messages(command: Command, budget_meta: dict[str, Any] | None = None) -> Command:
    return apply_agent_visible_budget(command, budget_meta)


def _truncate_agent_visible_result(result, budget_meta: dict[str, Any] | None = None):
    return apply_agent_visible_budget(result, budget_meta)


async def async_tool_call_wrapper(request, execute):
    """Wrap tool execution with hook interception and output truncation."""
    from core.hooks_manager import hooks_manager
    from core.native_tools import _raise_runtime_governance_exception_if_needed

    tool_name = request.tool_call.get("name", "unknown")
    budget_meta = tool_output_budget_for_request(request, tool_name)

    try:
        hooks_manager.execute_hook("on_tool_execute_start", tool=tool_name)
    except Exception as hook_err:
        _raise_runtime_governance_exception_if_needed(hook_err)
        error_msg = str(hook_err)
        print(f"[ToolWrapper] Hook blocked tool {tool_name}: {error_msg}")
        return apply_tool_surface_budget(
            ToolMessage(
                content=(
                    f"Error executing tool '{tool_name}': Intercepted and blocked by a system hook. "
                    f"Reason: {error_msg}\nDo not attempt this tool call again."
                ),
                name=tool_name,
                tool_call_id=request.tool_call.get("id", ""),
            ),
            budget_meta,
            tool_name=tool_name,
        )

    try:
        result = await execute(request)
    except Exception as execution_err:
        _raise_runtime_governance_exception_if_needed(execution_err)
        error_msg = str(execution_err)
        print(f"[ToolWrapper] Tool {tool_name} failed: {error_msg}")
        if str(tool_name or "").startswith("network_") and "__pregel_scratchpad" in error_msg:
            from runtimes.network_supervisor.compat_errors import CompatBridgeHardStop

            raise CompatBridgeHardStop(
                f"External client tool bridge hard stop for '{tool_name}': missing LangGraph interrupt context "
                "(__pregel_scratchpad). The model must not retry this network_* tool in the same run."
            ) from execution_err
        return apply_tool_surface_budget(
            ToolMessage(
                content=(
                    f"Error executing tool '{tool_name}': {error_msg}\n"
                    "Do not attempt this tool call again unless the user changes the request or provides missing information."
                ),
                name=tool_name,
                tool_call_id=request.tool_call.get("id", ""),
                status="error",
            ),
            budget_meta,
            tool_name=tool_name,
        )

    try:
        hooks_manager.execute_hook("on_tool_execute_end", tool=tool_name)
    except Exception as hook_err:
        _raise_runtime_governance_exception_if_needed(hook_err)

    return apply_agent_visible_budget(result, budget_meta)


def create_routed_tool_node(tools, name, fallback_goto):
    """Return a ToolNode wrapper that always routes explicitly via Command."""
    base_node = ToolNode(
        tools,
        name=name,
        handle_tool_errors=False,
        awrap_tool_call=async_tool_call_wrapper,
    )

    def _patch_command_goto(cmd):
        if isinstance(cmd, Command) and not getattr(cmd, "goto", None):
            return Command(goto=fallback_goto, update=cmd.update)
        return cmd

    async def routed_node(state, config=None, runtime=None):
        from langgraph.config import CONF, CONFIG_KEY_RUNTIME
        from langgraph.runtime import Runtime

        invoke_config = dict(config or {})
        configurable = dict(invoke_config.get(CONF) or {})
        if runtime is not None:
            configurable[CONFIG_KEY_RUNTIME] = runtime
        else:
            configurable.setdefault(CONFIG_KEY_RUNTIME, Runtime())
        invoke_config[CONF] = configurable

        result = await base_node.ainvoke(state, config=invoke_config)

        if isinstance(result, list):
            if any(isinstance(item, Command) for item in result):
                return [
                    _patch_command_goto(item) if isinstance(item, Command) else item
                    for item in result
                ]
            return Command(goto=fallback_goto, update={})

        if isinstance(result, dict):
            return Command(goto=fallback_goto, update=result)

        if isinstance(result, Command):
            return _patch_command_goto(result)

        return Command(goto=fallback_goto, update={})

    return routed_node
