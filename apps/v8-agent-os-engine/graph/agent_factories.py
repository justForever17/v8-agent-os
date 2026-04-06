import datetime
import hashlib
import logging
import platform
import re
import uuid
from pathlib import Path
from typing import Annotated, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, StructuredTool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, Field

from core.context.delegation import build_delegation_context, latest_delegation_context
from core.context_governance import emit_context_prepared_event
from core.context_orchestrator import context_orchestrator
from core.runtime.extensions_runtime import extensions_runtime_service
from core.models.factory import llm_factory
from core.response_normalizer import ensure_reasoning_content
from core.storage import storage
from core.system_tools.baseline import select_baseline_system_tool_names
from core.workspace_guard import build_workspace_path_status
from core.workspace_resolution import workspace_resolution_service
from erc.runtime_context import get_runtime_context
from skills.loader import SkillLoader
from .tool_routing import create_routed_tool_node


def _canonical_tool_name(tool_ref) -> str:
    metadata = dict(getattr(tool_ref, "metadata", None) or {})
    return str(metadata.get("canonicalName") or getattr(tool_ref, "name", "")).strip()


def _raw_tool_name(tool_ref) -> str:
    metadata = dict(getattr(tool_ref, "metadata", None) or {})
    raw_name = str(metadata.get("rawName") or "").strip()
    if raw_name:
        return raw_name
    tool_name = str(getattr(tool_ref, "name", "")).strip()
    if tool_name.startswith("gateway."):
        return tool_name[len("gateway.") :].strip()
    if "." in tool_name:
        return tool_name.split(".", 1)[1].strip()
    return tool_name


def _plugin_id(tool_ref) -> str:
    metadata = dict(getattr(tool_ref, "metadata", None) or {})
    return str(metadata.get("pluginId") or "").strip()


def _dedupe_tools(tools: list) -> list:
    seen: set[str] = set()
    deduped = []
    for tool_ref in tools:
        identity = str(getattr(tool_ref, "name", "")).strip() or _canonical_tool_name(tool_ref)
        if not identity or identity in seen:
            continue
        seen.add(identity)
        deduped.append(tool_ref)
    return deduped


def _resolved_tool_mode(agent_data: dict) -> str:
    configured = str(agent_data.get("tool_mode") or agent_data.get("toolMode") or "").strip()
    if configured in {"explicit", "contextual_auto"}:
        return configured
    selectors = list(agent_data.get("tools") or [])
    return "contextual_auto" if not selectors else "explicit"


def _tool_selector_matches(tool_ref, selector: str) -> bool:
    normalized = str(selector or "").strip()
    if not normalized:
        return False
    candidates = {
        str(getattr(tool_ref, "name", "")).strip(),
        _canonical_tool_name(tool_ref),
        _raw_tool_name(tool_ref),
    }
    plugin_id = _plugin_id(tool_ref)
    raw_name = _raw_tool_name(tool_ref)
    if plugin_id:
        candidates.add(plugin_id)
        if raw_name:
            candidates.add(f"{plugin_id}.{raw_name}")
    return normalized in {item for item in candidates if item}


def _resolve_selected_mcp_tools(all_mcp_tools: list, selectors: list[str]) -> list:
    selector_set = {str(item).strip() for item in selectors if str(item).strip()}
    if not selector_set:
        return []
    return [tool for tool in all_mcp_tools if str(getattr(tool, "name", "")).strip() in selector_set]


def _resolve_selected_plugin_host_tools(all_plugin_host_tools: list, selectors: list[str]) -> list:
    selector_set = {str(item).strip() for item in selectors if str(item).strip()}
    if not selector_set:
        return []
    return [tool for tool in all_plugin_host_tools if any(_tool_selector_matches(tool, selector) for selector in selector_set)]


def _extract_delegated_query(task_messages: list) -> str:
    for message in reversed(task_messages):
        if not isinstance(message, HumanMessage):
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        normalized = content.strip()
        if normalized.startswith("[Supervisor Delegated Task"):
            _, _, remainder = normalized.partition("]:")
            return remainder.strip() or normalized
        return normalized
    return ""


def _extract_latest_route_context(task_messages: list) -> dict[str, list[str] | str]:
    for message in reversed(task_messages):
        if not isinstance(message, AIMessage):
            continue
        additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
        payload = dict(additional_kwargs.get("v8_route_context") or {})
        if not payload:
            continue
        return {
            "query": str(payload.get("query") or "").strip(),
            "selectedSkillNames": [
                str(item).strip()
                for item in list(payload.get("selectedSkillNames") or [])
                if str(item).strip()
            ],
            "selectedSkillEntries": [
                item
                for item in list(payload.get("selectedSkillEntries") or [])
                if isinstance(item, dict)
            ],
            "selectedMcpTools": [
                str(item).strip()
                for item in list(payload.get("selectedMcpTools") or [])
                if str(item).strip()
            ],
            "selectedPluginHostTools": [
                str(item).strip()
                for item in list(payload.get("selectedPluginHostTools") or [])
                if str(item).strip()
            ],
        }
    return {
        "query": "",
        "selectedSkillNames": [],
        "selectedSkillEntries": [],
        "selectedMcpTools": [],
        "selectedPluginHostTools": [],
    }


def _resolve_selected_skill_names(selectors: list[str]) -> list[str]:
    selector_set = {str(item).strip() for item in selectors if str(item).strip()}
    if not selector_set:
        return []
    matched: list[str] = []
    for skill in SkillLoader.get_all_skills(force_refresh=False).values():
        skill_name = str(skill.get("name") or skill.get("folder") or "").strip()
        skill_folder = str(skill.get("folder") or "").strip()
        skill_path = str(skill.get("path") or "").strip()
        candidates = {value for value in {skill_name, skill_folder, skill_path} if value}
        if candidates & selector_set and skill_name and skill_name not in matched:
            matched.append(skill_name)
    return matched


def _resolved_workspace_prompt_path() -> str:
    raw_workspace_path = str(storage.get_workspace_config().get("agent_workspace_path") or "").strip()
    if raw_workspace_path:
        status = build_workspace_path_status(raw_workspace_path)
        if status.get("isLegacyResidue"):
            return str(status.get("recommendedPath") or workspace_resolution_service.get_main_workspace_path())
        return str(Path(raw_workspace_path).expanduser())
    return workspace_resolution_service.get_main_workspace_path()


def _resolve_inherited_route_context(state: dict, task_messages: list, *, agent_id: str) -> dict[str, list[str] | str]:
    delegated = latest_delegation_context(list(state.get("delegation_contexts") or []), agent_id=agent_id)
    if any(delegated.get(key) for key in ("selectedSkillNames", "selectedSkillEntries", "selectedMcpTools", "selectedPluginHostTools", "selectedBaselineTools", "query")):
        return delegated
    current_route_context = dict(state.get("current_route_context") or {})
    if any(current_route_context.get(key) for key in ("selectedSkillNames", "selectedSkillEntries", "selectedMcpTools", "selectedPluginHostTools", "selectedBaselineTools", "query")):
        return current_route_context
    legacy = _extract_latest_route_context(task_messages)
    return build_delegation_context(
        mode="legacy",
        query=legacy.get("query"),
        selected_skill_names=legacy.get("selectedSkillNames"),
        selected_skill_entries=legacy.get("selectedSkillEntries"),
        selected_mcp_tools=legacy.get("selectedMcpTools"),
        selected_plugin_host_tools=legacy.get("selectedPluginHostTools"),
    )


def build_handoff_tool(agent_id: str, agent_name: str, agent_desc: str):
    class HandoffInput(BaseModel):
        reason: str = Field(description=f"Detailed instructions, task description, and context for {agent_name} to execute.")

    ascii_id = re.sub(r"[^a-zA-Z0-9_-]", "", agent_id.replace("-", "_")).strip("_")
    if not ascii_id:
        ascii_id = hashlib.md5(agent_id.encode()).hexdigest()[:8]
    safe_tool_name = f"handoff_to_{ascii_id}"

    def handoff_with_ack(
        reason: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        state: Annotated[dict, InjectedState],
    ) -> Command:
        inherited_context = _resolve_inherited_route_context(dict(state or {}), list((state or {}).get("messages") or []), agent_id=agent_id)
        branch_context = build_delegation_context(
            agent_id=agent_id,
            agent_name=agent_name,
            query=reason,
            mode="serial",
            source_runtime_kind=inherited_context.get("sourceRuntimeKind"),
            selected_skill_names=inherited_context.get("selectedSkillNames"),
            selected_skill_entries=inherited_context.get("selectedSkillEntries"),
            selected_mcp_tools=inherited_context.get("selectedMcpTools"),
            selected_plugin_host_tools=inherited_context.get("selectedPluginHostTools"),
            selected_baseline_tools=inherited_context.get("selectedBaselineTools"),
            prompt_addition=inherited_context.get("promptAddition"),
            invocation_id=inherited_context.get("invocationId"),
        )
        return Command(
            goto=agent_id,
            update={
                "messages": [
                    ToolMessage(content=f"Successfully delegated to {agent_name}", tool_call_id=tool_call_id),
                    HumanMessage(content=f"[Supervisor Delegated Task to {agent_name}]:\n{reason}"),
                ],
                "delegation_contexts": [branch_context],
                "current_route_context": branch_context,
            },
        )

    return StructuredTool.from_function(
        func=handoff_with_ack,
        name=safe_tool_name,
        description=f"Hand off a specialized task to: {agent_name}. Description: {agent_desc}",
        args_schema=HandoffInput,
    )


def build_agent_node(
    *,
    agent_id: str,
    agent_name: str,
    agent_system_prompt: str,
    agent_tool_selectors: list[str],
    agent_tool_mode: str,
    all_mcp_tools: list,
    all_plugin_host_tools: list,
    filtered_native_tools: list,
    fetch_skill_instructions_tool,
    reflection_enabled: bool,
    agent_model_id: str | None,
    default_agent_llm,
    supervisor_model_id: str,
    robust_invoke: Callable,
    build_failure_command: Callable,
    extract_task_context: Callable,
    resolve_todos: Callable,
    sanitize_message_chain: Callable,
    sanitize_response_tool_calls: Callable,
):
    agent_specific_llm = (
        llm_factory.create_chat_model(agent_model_id, streaming=True)
        if agent_model_id
        else default_agent_llm
    )

    def agent_node_func(state):
        try:
            messages = state["messages"]

            workspace_path = _resolved_workspace_prompt_path()
            os_name = platform.system()
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            env_context = (
                f"<environment>\n"
                f"OS: {os_name}\n"
                f"Current Time: {current_time}\n"
                f"Local Workspace Absolute Path: {workspace_path}\n"
                f"When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, you MUST save them to the Local Workspace above.\n"
                "To display a workspace file in the chat, return a markdown image or link using the same-origin URL format: /api/workspace/files/YOUR_FILE_NAME\n"
                f"</environment>\n"
            )

            raw_todos = state.get("todos", [])
            active_plan_context = ""
            if raw_todos:
                todos_data = resolve_todos(raw_todos)
                task_info = todos_data.get("task_info", {})
                resolved_items = todos_data.get("items", [])
                is_active = any(item.get("status") in ("pending", "in_progress") for item in resolved_items)

                if is_active and task_info.get("plan"):
                    active_plan_context = (
                        f"\n<active_task_plan>\n"
                        f"Task Name: {task_info.get('name', 'Unnamed Task')}\n"
                        f"Supervisor has delegated a portion of this grand plan to you.\n"
                        f"Here is the context of the overall plan and current progress:\n\n"
                        f"<plan_details>\n{task_info['plan']}\n</plan_details>\n\n"
                        f"<current_progress>\n"
                    )
                    icon_map = {"done": "✓", "in_progress": "→", "pending": " ", "skipped": "⊘"}
                    for i, item in enumerate(resolved_items):
                        icon = icon_map.get(item.get("status", "pending"), " ")
                        active_plan_context += f"  [{icon}] #{i}: {item.get('text', '???')}\n"
                    active_plan_context += "</current_progress>\n</active_task_plan>\n"

            task_messages = extract_task_context(messages)
            delegated_query = _extract_delegated_query(task_messages)
            inherited_route_context = _resolve_inherited_route_context(state, task_messages, agent_id=agent_id)
            delegated_query = str(inherited_route_context.get("query") or delegated_query).strip() or delegated_query
            base_tools = _dedupe_tools(list(filtered_native_tools) + [fetch_skill_instructions_tool])
            selected_mcp_tools = _resolve_selected_mcp_tools(all_mcp_tools, agent_tool_selectors)
            selected_plugin_host_tools = _resolve_selected_plugin_host_tools(all_plugin_host_tools, agent_tool_selectors)
            explicit_skill_names = _resolve_selected_skill_names(agent_tool_selectors)

            if agent_tool_mode == "explicit":
                available_tools = _dedupe_tools(base_tools + selected_mcp_tools + selected_plugin_host_tools)
                inherited_skill_names: list[str] = explicit_skill_names
            else:
                inherited_mcp_tools = _resolve_selected_mcp_tools(
                    all_mcp_tools,
                    list(inherited_route_context.get("selectedMcpTools") or []),
                )
                inherited_plugin_host_tools = _resolve_selected_plugin_host_tools(
                    all_plugin_host_tools,
                    list(inherited_route_context.get("selectedPluginHostTools") or []),
                )
                inherited_skill_names = list(inherited_route_context.get("selectedSkillNames") or [])
                if inherited_mcp_tools or inherited_plugin_host_tools or inherited_skill_names:
                    available_tools = _dedupe_tools(base_tools + inherited_mcp_tools + inherited_plugin_host_tools)
                else:
                    available_tools = _dedupe_tools(base_tools + list(all_mcp_tools) + list(all_plugin_host_tools))

            route_context_token = extensions_runtime_service.bind_execution_context(
                session_id=state.get("session_id"),
                conversation_id=state.get("session_id"),
                run_id=state.get("run_id"),
                agent_id=agent_id,
            )
            try:
                route_bundle = extensions_runtime_service.build_contextual_route(
                    user_query=delegated_query,
                    available_tools=available_tools,
                    loaded_agents=None,
                    inherited_skill_names=inherited_skill_names,
                    skill_limit=4,
                    mcp_limit=6,
                    plugin_host_limit=6,
                )
                extensions_runtime_service.emit_route_selected(user_query=delegated_query, route_bundle=route_bundle)
                combined_tools = route_bundle.filtered_tools
            finally:
                extensions_runtime_service.reset_execution_context(route_context_token)

            sys_msg = SystemMessage(
                content=(
                    f"<system_persona>\nYou are a specialized agent named {agent_name}.\n{agent_system_prompt}\n</system_persona>\n\n"
                    f"{env_context}{active_plan_context}{route_bundle.prompt_addition}\n\n"
                    "[Interactive CLI Rule]\n"
                    "If you need to use an interactive CLI or REPL (examples: qwen, python REPL, node REPL, powershell, bash, cmd), NEVER use sync mode.\n"
                    "You MUST use `run_system_command` with `mode=session`, then inspect with `read_background_output`, send replies with `send_background_input`, and clean up with `terminate_background_command`.\n"
                    "If the environment reports that TTY/interactive automation is unavailable, stop retrying and return a concise failure summary to the supervisor.\n\n"
                    "When you have fully completed your assigned task, respond with your findings or status to return control to the supervisor."
                )
            )

            run_messages = [sys_msg] + task_messages
            run_messages = [ensure_reasoning_content(m) for m in run_messages]
            run_messages = sanitize_message_chain(run_messages)
            prepared_context = context_orchestrator.prepare(
                messages=run_messages,
                runtime_kind=str(get_runtime_context().get("runtime_kind") or "chat"),
                target_role=f"agent:{agent_id}",
                resolved_model_id=agent_model_id or supervisor_model_id,
            )
            run_messages = prepared_context.messages
            emit_context_prepared_event(
                prepared_context.audit,
                component="graph",
                node=agent_id,
                agent_id=agent_id,
            )

            route_context_record = {
                **build_delegation_context(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    query=delegated_query,
                    mode=str(inherited_route_context.get("mode") or "serial"),
                    source_runtime_kind=inherited_route_context.get("sourceRuntimeKind") or "chat",
                    selected_skill_names=route_bundle.selected_skill_names,
                    selected_skill_entries=route_bundle.candidate_summary.get("skillEntries") or [],
                    selected_mcp_tools=route_bundle.exposed_mcp_tool_names,
                    selected_plugin_host_tools=route_bundle.candidate_summary.get("pluginHostTools") or [],
                    selected_baseline_tools=select_baseline_system_tool_names(combined_tools),
                    prompt_addition=route_bundle.prompt_addition,
                    invocation_id=inherited_route_context.get("invocationId"),
                ),
                "toolMode": agent_tool_mode,
                "inheritedSkillNames": inherited_skill_names,
            }

            from core.automation.hooks import hooks_manager

            try:
                hooks_manager.execute_hook("on_agent_start", agent_name=agent_name, agent_id=agent_id)
            except Exception as hook_err:
                fallback_msg = AIMessage(content="I was stopped before I could even start generating.", id=str(uuid.uuid4()))
                feedback_msg = HumanMessage(
                    content=f"[System Hook Interception]: Your startup was rejected by a system hook. Reason: {hook_err}"
                )
                return Command(
                    goto=f"{agent_id}_reviewer" if reflection_enabled else "supervisor",
                    update={
                        "messages": [fallback_msg, feedback_msg],
                        "delegation_contexts": [route_context_record],
                        "current_route_context": route_context_record,
                    },
                )

            route_context_token = extensions_runtime_service.bind_execution_context(
                session_id=state.get("session_id"),
                conversation_id=state.get("session_id"),
                run_id=state.get("run_id"),
                agent_id=agent_id,
            )
            try:
                response = robust_invoke(
                    agent_specific_llm,
                    run_messages,
                    combined_tools,
                    role=f"agent:{agent_id}",
                    preferred_model_id=agent_model_id or supervisor_model_id,
                    build_model=lambda candidate_model_id: llm_factory.create_chat_model(
                        candidate_model_id,
                        streaming=True,
                        _role=f"agent:{agent_id}",
                    ),
                )
                extensions_runtime_service.emit_response_tool_calls(response)
                extensions_runtime_service.emit_execution_completed(response=response)
            finally:
                extensions_runtime_service.reset_execution_context(route_context_token)

            try:
                hooks_manager.execute_hook(
                    "on_agent_end",
                    agent_name=agent_name,
                    agent_id=agent_id,
                    response_content=response.content,
                )
            except Exception as hook_err:
                fallback_msg = AIMessage(
                    content="I generated an output, but it was intercepted by a system hook.",
                    id=str(uuid.uuid4()),
                )
                feedback_msg = HumanMessage(
                    content=(
                        "[System Hook Interception]: Your output was rejected by a system hook. "
                        f"Reason: {hook_err}\nPlease fix this immediately."
                    )
                )
                return Command(
                    goto=f"{agent_id}_reviewer" if reflection_enabled else "supervisor",
                    update={
                        "messages": [fallback_msg, feedback_msg],
                        "delegation_contexts": [route_context_record],
                        "current_route_context": route_context_record,
                    },
                )

            response = sanitize_response_tool_calls(response)

            if getattr(response, "tool_calls", None):
                return Command(
                    goto=f"{agent_id}_tools",
                    update={
                        "messages": [response],
                        "delegation_contexts": [route_context_record],
                        "current_route_context": route_context_record,
                    },
                )

            if reflection_enabled:
                return Command(
                    goto=f"{agent_id}_reviewer",
                    update={
                        "messages": [response],
                        "delegation_contexts": [route_context_record],
                        "current_route_context": route_context_record,
                    },
                )

            final_content = response.content if isinstance(response.content, str) else str(response.content)
            sub_tools_used = set()
            for message in state["messages"]:
                if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
                    for tool_call in message.tool_calls:
                        sub_tools_used.add(tool_call.get("name", "unknown"))

            refined_parts = [f"[{agent_name} 执行完毕]"]
            if sub_tools_used:
                refined_parts.append(f"使用工具: {', '.join(sorted(sub_tools_used))}")
            refined_parts.append(f"结果: {final_content}")
            refined_parts.append(
                "\n[System Instruction]: The exact detailed output above has ALREADY been streamed and displayed "
                "to the user in a specialized UI panel. DO NOT repeat, regurgitate, or summarize this output. "
                "Simply acknowledge the completion of the sub-agent's task and move to your next step."
            )
            refined_msg = HumanMessage(content="\n".join(refined_parts), id=str(uuid.uuid4()))
            return Command(
                goto="supervisor",
                update={
                    "messages": [refined_msg],
                    "delegation_contexts": [route_context_record],
                    "current_route_context": route_context_record,
                },
            )
        except Exception as exc:
            logging.getLogger("v8chat.supervisor").exception(
                "Sub-agent '%s' crashed during delegated execution",
                agent_id,
            )
            return build_failure_command(agent_name=agent_name, exc=exc)

    return agent_node_func


def build_reviewer_node(
    *,
    agent_id: str,
    agent_name: str,
    max_reflections: int,
    agent_model_id: str | None,
    default_agent_llm,
    supervisor_model_id: str,
    robust_invoke: Callable,
    build_failure_command: Callable,
    sanitize_message_chain: Callable,
):
    agent_specific_llm = (
        llm_factory.create_chat_model(agent_model_id, streaming=True)
        if agent_model_id
        else default_agent_llm
    )

    def reviewer_node_func(state):
        try:
            messages = state["messages"]
            reflection_count = sum(
                1
                for message in reversed(messages)
                if isinstance(message, HumanMessage) and "[Reflection Feedback from Reviewer" in str(message.content)
            )

            if reflection_count >= max_reflections:
                return Command(goto="supervisor", update={"messages": []})

            reviewer_sys = SystemMessage(
                content=(
                    f"You are a Senior Reviewer inspecting the output of {agent_name}.\n"
                    "Your job is to check for logic errors, incomplete implementations, or failure to follow instructions.\n"
                    "If the output is excellent and complete, respond ONLY with 'APPROVE'.\n"
                    "If there are issues, provide concise, constructive feedback on what needs to be fixed. Do not write the code yourself."
                )
            )
            run_messages = [reviewer_sys] + [m for m in messages if not isinstance(m, SystemMessage)]
            run_messages = [ensure_reasoning_content(m) for m in run_messages]
            run_messages = sanitize_message_chain(run_messages)

            from core.automation.hooks import hooks_manager

            hooks_manager.execute_hook("on_reviewer_start", agent_name=agent_name, agent_id=agent_id)
            response = robust_invoke(
                agent_specific_llm,
                run_messages,
                None,
                role=f"reviewer:{agent_id}",
                preferred_model_id=agent_model_id or supervisor_model_id,
                build_model=lambda candidate_model_id: llm_factory.create_chat_model(
                    candidate_model_id,
                    streaming=True,
                    _role=f"reviewer:{agent_id}",
                ),
            )
            hooks_manager.execute_hook("on_reviewer_end", agent_name=agent_name, agent_id=agent_id)

            content = str(response.content).strip()
            if "APPROVE" in content or "approve" in content.lower():
                cap_msg = HumanMessage(
                    content=(
                        f"[{agent_name} 执行完毕且通过审核]\n\n"
                        "[System Instruction]: The exact detailed output of "
                        f"{agent_name} has ALREADY been streamed and displayed to the user in a specialized UI panel. "
                        "DO NOT repeat, regurgitate, or summarize their output. Simply acknowledge the completion "
                        "of the sub-agent's task and move to your next step."
                    ),
                    id=str(uuid.uuid4()),
                )
                return Command(goto="supervisor", update={"messages": [response, cap_msg]})

            feedback_msg = HumanMessage(
                content=f"[Reflection Feedback from Reviewer to {agent_name}]:\nYour previous output has issues. Please fix them:\n{content}"
            )
            return Command(goto=agent_id, update={"messages": [response, feedback_msg]})
        except Exception as exc:
            logging.getLogger("v8chat.supervisor").exception(
                "Reviewer for sub-agent '%s' crashed during reflection",
                agent_id,
            )
            return build_failure_command(agent_name=f"{agent_name} Reviewer", exc=exc)

    return reviewer_node_func


def build_specialist_agent_components(
    *,
    loaded_agents: list[dict],
    all_mcp_tools: list,
    all_plugin_host_tools: list,
    filtered_native_tools: list,
    default_agent_llm,
    supervisor_model_id: str,
    robust_invoke: Callable,
    build_failure_command: Callable,
    extract_task_context: Callable,
    resolve_todos: Callable,
    sanitize_message_chain: Callable,
    sanitize_response_tool_calls: Callable,
    fetch_skill_instructions,
):
    handoff_tools = []
    agent_nodes_map = {}

    for agent_data in loaded_agents:
        agent_id = agent_data["id"]
        if agent_id == "supervisor":
            continue

        agent_name = agent_data["name"]
        agent_desc = agent_data.get("description", "")
        agent_sys = agent_data.get("system_prompt", "")
        tool_selectors = [str(item).strip() for item in list(agent_data.get("tools") or []) if str(item).strip()]
        agent_model = agent_data.get("model")
        reflection_enabled = agent_data.get("reflection_enabled", False)
        max_reflections = agent_data.get("max_reflections", 3)
        agent_tool_mode = _resolved_tool_mode(agent_data)

        if agent_tool_mode == "contextual_auto":
            tool_node_tools = _dedupe_tools(list(filtered_native_tools) + [fetch_skill_instructions] + list(all_mcp_tools) + list(all_plugin_host_tools))
        else:
            tool_node_tools = _dedupe_tools(
                list(filtered_native_tools)
                + [fetch_skill_instructions]
                + _resolve_selected_mcp_tools(all_mcp_tools, tool_selectors)
                + _resolve_selected_plugin_host_tools(all_plugin_host_tools, tool_selectors)
            )

        handoff_tools.append(build_handoff_tool(agent_id, agent_name, agent_desc))
        agent_nodes_map[agent_id] = {
            "node_func": build_agent_node(
                agent_id=agent_id,
                agent_name=agent_name,
                agent_system_prompt=agent_sys,
                agent_tool_selectors=tool_selectors,
                agent_tool_mode=agent_tool_mode,
                all_mcp_tools=all_mcp_tools,
                all_plugin_host_tools=all_plugin_host_tools,
                filtered_native_tools=filtered_native_tools,
                fetch_skill_instructions_tool=fetch_skill_instructions,
                reflection_enabled=reflection_enabled,
                agent_model_id=agent_model,
                default_agent_llm=default_agent_llm,
                supervisor_model_id=supervisor_model_id,
                robust_invoke=robust_invoke,
                build_failure_command=build_failure_command,
                extract_task_context=extract_task_context,
                resolve_todos=resolve_todos,
                sanitize_message_chain=sanitize_message_chain,
                sanitize_response_tool_calls=sanitize_response_tool_calls,
            ),
            "tools": tool_node_tools,
            "tool_node_func": create_routed_tool_node(tool_node_tools, name=f"{agent_id}_tools", fallback_goto=agent_id)
            if tool_node_tools
            else None,
            "tool_mode": agent_tool_mode,
            "reflection_enabled": reflection_enabled,
            "reviewer_func": (
                build_reviewer_node(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    max_reflections=max_reflections,
                    agent_model_id=agent_model,
                    default_agent_llm=default_agent_llm,
                    supervisor_model_id=supervisor_model_id,
                    robust_invoke=robust_invoke,
                    build_failure_command=build_failure_command,
                    sanitize_message_chain=sanitize_message_chain,
                )
                if reflection_enabled
                else None
            ),
        }

    return handoff_tools, agent_nodes_map
