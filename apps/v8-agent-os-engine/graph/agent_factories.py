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
from core.delegation_broker import build_minimal_task_brief, task_brief_query_text
from core.context_governance import emit_context_prepared_event
from core.context_orchestrator import context_orchestrator
from core.native_tools import delegation_broker
from core.runtime.extensions_runtime import extensions_runtime_service
from core.models.factory import llm_factory
from core.response_normalizer import ensure_reasoning_content
from core.storage import storage
from core.system_tools.baseline import select_baseline_system_tool_names
from core.time_truth import utc_now_iso
from core.workspace_guard import build_workspace_path_status
from core.workspace_resolution import workspace_resolution_service
from erc.runtime_context import get_runtime_context
from .tool_routing import create_routed_tool_node
from .route_context import merge_route_context


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


def _exclude_supervisor_only_tools(tools: list) -> list:
    excluded = {"delegation_broker"}
    return [
        tool_ref
        for tool_ref in list(tools or [])
        if str(getattr(tool_ref, "name", "")).strip() not in excluded
    ]


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
        if "[USER REQUEST]" in normalized and "[/USER REQUEST]" in normalized:
            _prefix, _marker, remainder = normalized.partition("[USER REQUEST]")
            body, _closing, _suffix = remainder.partition("[/USER REQUEST]")
            extracted = body.strip()
            if extracted:
                return extracted
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
            "selectedSkillIds": [
                str(item).strip()
                for item in list(payload.get("selectedSkillIds") or [])
                if str(item).strip()
            ],
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
            "skillRootDescriptors": [
                item
                for item in list(payload.get("skillRootDescriptors") or [])
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
        "selectedSkillIds": [],
        "selectedSkillNames": [],
        "selectedSkillEntries": [],
        "skillRootDescriptors": [],
        "selectedMcpTools": [],
        "selectedPluginHostTools": [],
    }


def _resolve_selected_skills(selectors: list[str], *, state: dict | None = None) -> tuple[list[str], list[str]]:
    selector_set = {str(item).strip() for item in selectors if str(item).strip()}
    if not selector_set:
        return [], []
    matched_ids: list[str] = []
    matched_names: list[str] = []
    for skill in extensions_runtime_service.list_skills(
        force_refresh=False,
        prefer_cached_ready_inventory=True,
        session_id=(state or {}).get("session_id"),
        explicit_workspace_id=(state or {}).get("workspace_id"),
        explicit_workspace_path=(state or {}).get("workspace_path"),
        explicit_project_id=(state or {}).get("project_id"),
        runtime_kind="chat",
    ):
        skill_id = str(skill.get("skillId") or "").strip()
        skill_name = str(skill.get("name") or skill.get("folder") or "").strip()
        skill_folder = str(skill.get("folder") or "").strip()
        skill_path = str(skill.get("path") or "").strip()
        candidates = {value for value in {skill_id, skill_name, skill_folder, skill_path} if value}
        if candidates & selector_set:
            if skill_id and skill_id not in matched_ids:
                matched_ids.append(skill_id)
            if skill_name and skill_name not in matched_names:
                matched_names.append(skill_name)
    return matched_ids, matched_names


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
    if any(delegated.get(key) for key in ("selectedSkillIds", "selectedSkillNames", "selectedSkillEntries", "skillRootDescriptors", "selectedMcpTools", "selectedPluginHostTools", "selectedBaselineTools", "query")):
        return delegated
    current_route_context = dict(state.get("current_route_context") or {})
    if any(current_route_context.get(key) for key in ("selectedSkillIds", "selectedSkillNames", "selectedSkillEntries", "skillRootDescriptors", "selectedMcpTools", "selectedPluginHostTools", "selectedBaselineTools", "query")):
        return current_route_context
    legacy = _extract_latest_route_context(task_messages)
    return build_delegation_context(
        mode="legacy",
        query=legacy.get("query"),
        selected_skill_ids=legacy.get("selectedSkillIds"),
        selected_skill_names=legacy.get("selectedSkillNames"),
        selected_skill_entries=legacy.get("selectedSkillEntries"),
        skill_root_descriptors=legacy.get("skillRootDescriptors"),
        selected_mcp_tools=legacy.get("selectedMcpTools"),
        selected_plugin_host_tools=legacy.get("selectedPluginHostTools"),
    )


def _merged_route_context(state: dict | None, overlay: dict | None) -> dict:
    return merge_route_context(
        dict((state or {}).get("current_route_context") or {}),
        dict(overlay or {}),
    )


def build_contextual_auto_tool_node(
    *,
    base_tools: list,
    all_mcp_tools: list,
    all_plugin_host_tools: list,
    name: str,
    fallback_goto: str,
):
    async def contextual_tool_node(state):
        route_context = dict((state or {}).get("current_route_context") or {})
        selected_mcp_tools = _resolve_selected_mcp_tools(
            all_mcp_tools,
            list(route_context.get("selectedMcpTools") or []),
        )
        selected_plugin_host_tools = _resolve_selected_plugin_host_tools(
            all_plugin_host_tools,
            list(route_context.get("selectedPluginHostTools") or []),
        )
        tools = _dedupe_tools(list(base_tools or []) + selected_mcp_tools + selected_plugin_host_tools)
        routed = create_routed_tool_node(tools, name=name, fallback_goto=fallback_goto)
        return await routed(state)

    return contextual_tool_node


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
        task_brief = build_minimal_task_brief(
            goal=reason,
            preferred_agent_id=agent_id,
            execution_lane_hint="subagent",
        )
        return delegation_broker.func(
            mode="dispatch",
            tasks=[task_brief],
            tool_call_id=tool_call_id,
            state={**dict(state or {}), "delegationCompatSource": safe_tool_name},
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
            current_time = utc_now_iso()
            env_context = (
                f"<environment>\n"
                f"OS: {os_name}\n"
                f"Current Time: {current_time}\n"
                f"Local Workspace Absolute Path: {workspace_path}\n"
                f"When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, you MUST save them to the Local Workspace above.\n"
                "Do NOT expose raw local filesystem paths, raw /api/workspace/files links, or raw <img>/<video>/<audio> HTML in the final reply. "
                "Reference generated media naturally in prose and rely on the runtime artifact/resource pipeline for rendering.\n"
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
            delegated_task_brief = inherited_route_context.get("taskBrief") if isinstance(inherited_route_context.get("taskBrief"), dict) else None
            delegated_query = task_brief_query_text(delegated_task_brief) or str(inherited_route_context.get("query") or delegated_query).strip() or delegated_query
            base_tools = _dedupe_tools(_exclude_supervisor_only_tools(list(filtered_native_tools) + [fetch_skill_instructions_tool]))
            selected_mcp_tools = _resolve_selected_mcp_tools(all_mcp_tools, agent_tool_selectors)
            selected_plugin_host_tools = _resolve_selected_plugin_host_tools(all_plugin_host_tools, agent_tool_selectors)
            explicit_skill_ids, explicit_skill_names = _resolve_selected_skills(agent_tool_selectors, state=state)

            if agent_tool_mode == "explicit":
                available_tools = _dedupe_tools(base_tools + selected_mcp_tools + selected_plugin_host_tools)
                inherited_skill_ids: list[str] = explicit_skill_ids
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
                inherited_skill_ids = list(inherited_route_context.get("selectedSkillIds") or [])
                inherited_skill_names = list(inherited_route_context.get("selectedSkillNames") or [])
                if inherited_mcp_tools or inherited_plugin_host_tools or inherited_skill_ids or inherited_skill_names:
                    available_tools = _dedupe_tools(base_tools + inherited_mcp_tools + inherited_plugin_host_tools)
                else:
                    available_tools = _dedupe_tools(base_tools + list(all_mcp_tools) + list(all_plugin_host_tools))

            route_context_token = extensions_runtime_service.bind_execution_context(
                session_id=state.get("session_id"),
                conversation_id=state.get("session_id"),
                run_id=state.get("run_id"),
                agent_id=agent_id,
                workspace_id=state.get("workspace_id"),
                workspace_path=state.get("workspace_path"),
                project_id=state.get("project_id"),
                runtime_kind="chat",
            )
            try:
                route_bundle = extensions_runtime_service.build_contextual_route(
                    user_query=delegated_query,
                    available_tools=available_tools,
                    loaded_agents=None,
                    inherited_skill_ids=inherited_skill_ids,
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
                    "You MUST use `command_session_broker(mode=start)` for long-running or interactive terminal sessions.\n"
                    "After a session starts, use `command_session_broker(mode=observe|input|terminate)` to inspect, continue, and finish it.\n"
                    "For known AI CLIs, the broker may automatically enable the `chat_cli` profile so that observe returns only the latest semantic delta instead of replaying the whole accumulated screen.\n"
                    "For `interactive + tty + terminal_screen` sessions, treat `screenSnapshot`, `observationState`, `awaitingInput`, and `status` as the primary truth.\n"
                    "If observe reports that the CLI still has more reply to emit, keep polling or use `wait(seconds, note)` before polling again; do NOT assume the model stalled just because it did not replay the full transcript.\n"
                    "If the prompt/input box is already rendered and `awaitingInput=true`, the CLI is ready for dialogue even if MCP/debug banners are still visible.\n"
                    "When sending input, treat a rendered prompt as ready immediately. The broker accepts both actual newlines and common escaped sequences like `\\n` to represent Enter.\n"
                    "NEVER conclude that the CLI has stalled or produced no reply solely because appended text is empty; full-screen TUIs often redraw the screen in place.\n"
                    "If observation indicates `render_stalled`, report that V8 has not yet confirmed a new reply from the terminal observation chain instead of claiming the CLI definitely failed to answer.\n"
                    "If `encodingState` indicates mojibake or undecodable text, report that the terminal text is currently distorted instead of interpreting the corrupted content as a real answer.\n"
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
                    selected_skill_ids=route_bundle.selected_skill_ids,
                    selected_skill_names=route_bundle.selected_skill_names,
                    selected_skill_entries=route_bundle.candidate_summary.get("skillEntries") or [],
                    skill_root_descriptors=route_bundle.skill_root_descriptors or [],
                    selected_mcp_tools=route_bundle.exposed_mcp_tool_names,
                    selected_plugin_host_tools=route_bundle.candidate_summary.get("pluginHostTools") or [],
                    selected_baseline_tools=select_baseline_system_tool_names(combined_tools),
                    prompt_addition=route_bundle.prompt_addition,
                    invocation_id=inherited_route_context.get("invocationId"),
                    task_brief=delegated_task_brief,
                ),
                "toolMode": agent_tool_mode,
                "inheritedSkillIds": inherited_skill_ids,
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
                        "current_route_context": _merged_route_context(state, route_context_record),
                    },
                )

            route_context_token = extensions_runtime_service.bind_execution_context(
                session_id=state.get("session_id"),
                conversation_id=state.get("session_id"),
                run_id=state.get("run_id"),
                agent_id=agent_id,
                workspace_id=state.get("workspace_id"),
                workspace_path=state.get("workspace_path"),
                project_id=state.get("project_id"),
                runtime_kind="chat",
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
                        "current_route_context": _merged_route_context(state, route_context_record),
                    },
                )

            response = sanitize_response_tool_calls(response)

            if getattr(response, "tool_calls", None):
                return Command(
                    goto=f"{agent_id}_tools",
                    update={
                        "messages": [response],
                        "delegation_contexts": [route_context_record],
                        "current_route_context": _merged_route_context(state, route_context_record),
                    },
                )

            if reflection_enabled:
                return Command(
                    goto=f"{agent_id}_reviewer",
                    update={
                        "messages": [response],
                        "delegation_contexts": [route_context_record],
                        "current_route_context": _merged_route_context(state, route_context_record),
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
                    "current_route_context": _merged_route_context(state, route_context_record),
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

        contextual_base_tools = _dedupe_tools(_exclude_supervisor_only_tools(list(filtered_native_tools) + [fetch_skill_instructions]))
        if agent_tool_mode == "contextual_auto":
            # Contextual agents receive external MCP/PluginHost tools only after the
            # delegated task route selects them. Keep the static tool node narrow so
            # the runtime surface does not silently retain the full external tree.
            tool_node_tools = contextual_base_tools
            tool_node_func = build_contextual_auto_tool_node(
                base_tools=contextual_base_tools,
                all_mcp_tools=all_mcp_tools,
                all_plugin_host_tools=all_plugin_host_tools,
                name=f"{agent_id}_tools",
                fallback_goto=agent_id,
            )
        else:
            tool_node_tools = _dedupe_tools(
                _exclude_supervisor_only_tools(list(filtered_native_tools))
                + [fetch_skill_instructions]
                + _resolve_selected_mcp_tools(all_mcp_tools, tool_selectors)
                + _resolve_selected_plugin_host_tools(all_plugin_host_tools, tool_selectors)
            )
            tool_node_func = create_routed_tool_node(tool_node_tools, name=f"{agent_id}_tools", fallback_goto=agent_id) if tool_node_tools else None

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
            "tool_node_func": tool_node_func,
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
