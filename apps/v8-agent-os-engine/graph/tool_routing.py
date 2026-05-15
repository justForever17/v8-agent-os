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

SUPERVISOR_DIRECT_SCOPE_ALLOWED_TOOLS = {
    "delegation_broker",
    "runtime_broker",
    "ask_user",
    "write_todos",
    "update_todo",
}
SUPERVISOR_DIRECT_SCOPE_GATED_TOOLS = {
    "run_system_command",
    "command_session_broker",
    "write_native_file",
    "replace_native_file",
    "edit_native_file",
    "delete_native_file",
    "creative_media_create_job",
    "creative_media_retry_job",
    "computer_use_execute",
    "computer_use_click",
    "computer_use_type_text",
    "computer_use_drag",
}
SUPERVISOR_DIRECT_SCOPE_PROJECT_WRITE_TOOLS = {
    "write_native_file",
    "replace_native_file",
    "edit_native_file",
    "delete_native_file",
}


def _supervisor_direct_scope_operation_fingerprint(run_id: str) -> str:
    return f"supervisor_direct_scope_exception:{str(run_id or '').strip()}"


def _state_messages(state: Any) -> list[Any]:
    if isinstance(state, dict):
        messages = state.get("messages")
    else:
        messages = getattr(state, "messages", None)
    return list(messages or []) if isinstance(messages, list) else []


def _state_mapping(state: Any) -> dict[str, Any]:
    return dict(state or {}) if isinstance(state, dict) else {}


def _message_tool_calls(message: Any) -> list[dict[str, Any]]:
    calls = getattr(message, "tool_calls", None) or []
    normalized: list[dict[str, Any]] = []
    for call in list(calls or []):
        if isinstance(call, dict):
            normalized.append(call)
    if normalized:
        return normalized
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    for item in list(additional_kwargs.get("tool_calls") or []):
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        normalized.append(
            {
                "id": item.get("id"),
                "name": function.get("name") or item.get("name"),
                "args": function.get("arguments") or item.get("args"),
            }
        )
    return normalized


def _supervisor_direct_tool_names(state: Any, current_tool_call: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen_current = False
    current_id = str((current_tool_call or {}).get("id") or "").strip()
    current_name = str((current_tool_call or {}).get("name") or "").strip()
    for message in _state_messages(state):
        for call in _message_tool_calls(message):
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            if current_id and str(call.get("id") or "").strip() == current_id:
                seen_current = True
    if current_name and not seen_current:
        names.append(current_name)
    return names


def _supervisor_direct_scope_approved(run_id: str, operation_fingerprint: str) -> bool:
    if not run_id or not operation_fingerprint:
        return False
    try:
        from erc.run_service import run_service

        run_record = run_service.get_run(run_id)
    except Exception:
        return False
    operations = (dict((run_record or {}).get("metadata") or {})).get("approvedSafetyOperations")
    if not isinstance(operations, list):
        return False
    for item in operations:
        if not isinstance(item, dict):
            continue
        if str(item.get("fingerprint") or "").strip() != operation_fingerprint:
            continue
        if str(item.get("approval_kind") or "").strip() == "supervisor_direct_scope_exception":
            return True
    return False


def _maybe_raise_supervisor_direct_scope_gate(request: Any, *, tool_node_name: str = "") -> None:
    node_name = str(tool_node_name or "").strip()
    if node_name != "supervisor_tools":
        return
    tool_call = dict(getattr(request, "tool_call", {}) or {})
    tool_name = str(tool_call.get("name") or "").strip()
    if not tool_name or tool_name in SUPERVISOR_DIRECT_SCOPE_ALLOWED_TOOLS:
        return
    is_gated_tool = tool_name in SUPERVISOR_DIRECT_SCOPE_GATED_TOOLS or tool_name.startswith(("creative_media_", "computer_use_"))
    if not is_gated_tool:
        return

    state_mapping = _state_mapping(getattr(request, "state", None))
    planner_dispatch_status = dict(state_mapping.get("planner_dispatch_status") or {})
    if bool(planner_dispatch_status.get("blocked")):
        from core.model_governance_exceptions import ModelGovernanceInterventionRequired
        from erc.runtime_context import get_runtime_context

        runtime_context = get_runtime_context()
        run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
        operation_fingerprint = _supervisor_direct_scope_operation_fingerprint(run_id)
        if _supervisor_direct_scope_approved(run_id, operation_fingerprint):
            return
        payload = {
            "riskCode": "planner_auto_dispatch_blocked_direct_tool",
            "summary": "Planner 自动派发没有可用工程执行目标或被写集治理阻断，Supervisor 不能继续 direct 执行高影响工具。",
            "blockedTool": tool_name,
            "dispatchStatus": planner_dispatch_status,
            "allowedNextTools": ["delegation_broker", "runtime_broker", "ask_user"],
            "recommendedNextAction": "配置工程 subagent/external worker，修复 writeSet，或请求用户批准 direct exception。",
            "operationFingerprint": operation_fingerprint,
            "operationTargetFingerprint": operation_fingerprint,
        }
        raise ModelGovernanceInterventionRequired(
            "Planner auto-dispatch blocked direct tool execution.",
            approval_kind="supervisor_direct_scope_exception",
            question=(
                "Planner 自动派发没有找到可用工程执行目标，当前 Supervisor 想继续直接执行高影响工具。"
                "是否允许本轮继续 direct exception？"
            ),
            details=payload,
            request_payload={
                **payload,
                "approvalKind": "supervisor_direct_scope_exception",
                "interactionKind": "approval",
                "eventSummary": {
                    "actionFamily": "runtime_governance",
                    "operation": "planner_auto_dispatch_blocked_direct_tool",
                    "target": tool_name,
                    "riskCode": "planner_auto_dispatch_blocked_direct_tool",
                    "verdict": "review",
                    "reason": payload["summary"],
                    "nextAction": payload["recommendedNextAction"],
                },
            },
        )

    tool_names = _supervisor_direct_tool_names(getattr(request, "state", None), tool_call)
    tool_step_count = len([name for name in tool_names if name])
    project_write_count = len([name for name in tool_names if name in SUPERVISOR_DIRECT_SCOPE_PROJECT_WRITE_TOOLS])
    exceeded_reasons: list[str] = []
    if tool_step_count > 10:
        exceeded_reasons.append("tool_steps_gt_10")
    if project_write_count > 3:
        exceeded_reasons.append("project_file_writes_gt_3")
    if not exceeded_reasons:
        return

    from core.model_governance_exceptions import ModelGovernanceInterventionRequired
    from erc.runtime_context import get_runtime_context

    runtime_context = get_runtime_context()
    run_id = str(runtime_context.get("run_id") or runtime_context.get("runId") or "").strip()
    operation_fingerprint = _supervisor_direct_scope_operation_fingerprint(run_id)
    if _supervisor_direct_scope_approved(run_id, operation_fingerprint):
        return
    payload = {
        "riskCode": "supervisor_direct_scope_blocked",
        "summary": "Supervisor direct 执行已进入硬门禁；复杂任务后续可变更/长耗时工具必须先进入 delegation、Engineering discipline 或用户批准 direct exception。",
        "blockedTool": tool_name,
        "toolStepCount": tool_step_count,
        "projectWriteCount": project_write_count,
        "reasons": exceeded_reasons,
        "allowedNextTools": ["delegation_broker", "runtime_broker", "ask_user"],
        "recommendedNextAction": "调用 delegation_broker 派发 engineering family/external worker，或请求用户批准继续 direct exception。",
        "operationFingerprint": operation_fingerprint,
        "operationTargetFingerprint": operation_fingerprint,
    }
    raise ModelGovernanceInterventionRequired(
        "Supervisor direct scope gate requires routing or explicit approval.",
        approval_kind="supervisor_direct_scope_exception",
        question=(
            "Supervisor 已超过小任务 direct 执行阈值，当前想继续直接执行高影响工具。"
            "是否允许本轮继续 direct exception？也可以拒绝后让它改走 Engineering/delegation。"
        ),
        details=payload,
        request_payload={
            **payload,
            "approvalKind": "supervisor_direct_scope_exception",
            "interactionKind": "approval",
            "eventSummary": {
                "actionFamily": "runtime_governance",
                "operation": "supervisor_direct_scope_exception",
                "target": tool_name,
                "riskCode": "supervisor_direct_scope_blocked",
                "verdict": "review",
                "reason": payload["summary"],
                "nextAction": payload["recommendedNextAction"],
            },
        },
    )


def _tool_output_budget_for_request(request: Any, tool_name: str) -> dict[str, Any]:
    return tool_output_budget_for_request(request, tool_name)


def _truncate_tool_message_content(message: ToolMessage, budget_meta: dict[str, Any] | None = None) -> ToolMessage:
    return apply_tool_surface_budget(message, budget_meta)


def _truncate_command_tool_messages(command: Command, budget_meta: dict[str, Any] | None = None) -> Command:
    return apply_agent_visible_budget(command, budget_meta)


def _truncate_agent_visible_result(result, budget_meta: dict[str, Any] | None = None):
    return apply_agent_visible_budget(result, budget_meta)


async def async_tool_call_wrapper(request, execute, *, tool_node_name: str = ""):
    """Wrap tool execution with hook interception and output truncation."""
    from core.hooks_manager import hooks_manager
    from core.native_tools import _raise_runtime_governance_exception_if_needed

    tool_name = request.tool_call.get("name", "unknown")
    budget_meta = tool_output_budget_for_request(request, tool_name)
    _maybe_raise_supervisor_direct_scope_gate(request, tool_node_name=tool_node_name)

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
    async def _wrapped_tool_call(request, execute):
        return await async_tool_call_wrapper(request, execute, tool_node_name=name)

    base_node = ToolNode(
        tools,
        name=name,
        handle_tool_errors=False,
        awrap_tool_call=_wrapped_tool_call,
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
