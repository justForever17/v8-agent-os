from langchain_core.messages import ToolMessage
from langgraph.prebuilt import ToolNode
from langgraph.types import Command


MAX_TOOL_OUTPUT_LENGTH = 15000


async def async_tool_call_wrapper(request, execute):
    """Wrap tool execution with hook interception and output truncation."""
    from core.hooks_manager import hooks_manager

    tool_name = request.tool_call.get("name", "unknown")

    try:
        hooks_manager.execute_hook("on_tool_execute_start", tool=tool_name)
    except Exception as hook_err:
        error_msg = str(hook_err)
        print(f"[ToolWrapper] Hook blocked tool {tool_name}: {error_msg}")
        return ToolMessage(
            content=(
                f"Error executing tool '{tool_name}': Intercepted and blocked by a system hook. "
                f"Reason: {error_msg}\nDo not attempt this tool call again."
            ),
            name=tool_name,
            tool_call_id=request.tool_call.get("id", ""),
        )

    result = await execute(request)

    try:
        hooks_manager.execute_hook("on_tool_execute_end", tool=tool_name)
    except Exception:
        pass

    if isinstance(result, ToolMessage) and result.content:
        content_str = result.content if isinstance(result.content, str) else str(result.content)
        if len(content_str) > MAX_TOOL_OUTPUT_LENGTH:
            truncated = (
                f"{content_str[:MAX_TOOL_OUTPUT_LENGTH]}\n\n"
                f"...[OUTPUT TRUNCATED BY SYSTEM. Original length: {len(content_str)} chars]..."
            )
            result = ToolMessage(content=truncated, tool_call_id=result.tool_call_id, name=result.name)
        elif not isinstance(result.content, str):
            result = ToolMessage(content=content_str, tool_call_id=result.tool_call_id, name=result.name)

    return result


def create_routed_tool_node(tools, name, fallback_goto):
    """Return a ToolNode wrapper that always routes explicitly via Command."""
    base_node = ToolNode(
        tools,
        name=name,
        handle_tool_errors=True,
        awrap_tool_call=async_tool_call_wrapper,
    )

    def _patch_command_goto(cmd):
        if isinstance(cmd, Command) and not getattr(cmd, "goto", None):
            return Command(goto=fallback_goto, update=cmd.update)
        return cmd

    async def routed_node(state):
        result = await base_node.ainvoke(state)

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
