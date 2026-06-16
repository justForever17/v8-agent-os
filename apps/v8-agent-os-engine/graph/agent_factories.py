import json
import logging
import platform
import uuid
from typing import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from core.context.delegation import build_delegation_context, latest_delegation_context
from core.background_context_guard import prepare_background_model_messages
from core.delegation_broker import infer_engineering_task_role, task_brief_query_text, task_brief_route_query_text
from core.context_governance import emit_context_prepared_event
from core.context_orchestrator import context_orchestrator
from core.host_load import render_host_load_line
from core.safety_active_defense import render_host_alerts_line
from core.prompt_cache_segments import build_prompt_segments_from_parts
from core.runtime.extensions_runtime import extensions_runtime_service
from core.models.factory import llm_factory
from core.response_normalizer import ensure_reasoning_content
from core.system_tools.baseline import select_baseline_system_tool_names, select_baseline_system_tools
from core.runtime_tool_access import filter_visible_tools_for_actor, normalize_runtime_access, runtime_tool_names_for_groups
from core.time_truth import utc_now_iso
from core.workspace_capability import build_workspace_binding
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


def _select_contextual_subagent_native_tools(filtered_native_tools: list, runtime_access: list[str]) -> list:
    """Keep contextual_auto narrow while adding explicitly granted runtime tools."""
    baseline_tools = list(select_baseline_system_tools(filtered_native_tools))
    granted_runtime_tool_names = runtime_tool_names_for_groups(runtime_access)
    granted_runtime_tools = [
        tool_ref
        for tool_ref in list(filtered_native_tools or [])
        if str(getattr(tool_ref, "name", "")).strip() in granted_runtime_tool_names
    ]
    return _dedupe_tools(baseline_tools + granted_runtime_tools)


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


def _resolved_workspace_binding_for_state(state) -> dict:
    runtime_context = get_runtime_context()
    context = {
        "runtime_kind": str(runtime_context.get("runtime_kind") or "chat"),
        "session_id": (state or {}).get("session_id") or runtime_context.get("session_id"),
        "workspace_id": (state or {}).get("workspace_id") or runtime_context.get("workspace_id"),
        "workspace_path": (state or {}).get("workspace_path") or runtime_context.get("workspace_path"),
        "project_id": (state or {}).get("project_id") or runtime_context.get("project_id"),
    }
    return build_workspace_binding(context).as_dict()


def _compact_prompt_value(value) -> str:
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item).strip() for item in list(value) if str(item).strip())
    return str(value or "").strip()


def _truthy_task_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "y", "write", "required", "需要", "是", "有"}


def _task_brief_requires_artifact_write(task_brief: dict | None) -> bool:
    if not isinstance(task_brief, dict):
        return False
    if _truthy_task_value(task_brief.get("writeRequired") or task_brief.get("write_required")):
        return True
    deliverable_kind = str(task_brief.get("deliverableKind") or task_brief.get("deliverable_kind") or "").strip().lower()
    if deliverable_kind in {"artifact", "patch", "implementation", "skill_artifact", "project_artifact"}:
        return True
    context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    if _truthy_task_value(context.get("writeRequired") or context.get("write_required")):
        return True
    if str(context.get("artifactWriteDiscipline") or "").strip():
        return True
    blob = json.dumps(task_brief, ensure_ascii=False).lower()
    return any(
        marker in blob
        for marker in (
            "skill.md",
            ".agents/skills",
            "verification-report",
            "delivery-summary",
            "expectedoutputs",
            "expected_outputs",
            "预期输出",
            "写入",
            "创建文件",
            "生成文件",
        )
    )


def _artifact_write_discipline_lines(task_brief: dict | None) -> list[str]:
    if not _task_brief_requires_artifact_write(task_brief):
        return []
    context = task_brief.get("context") if isinstance(task_brief, dict) and isinstance(task_brief.get("context"), dict) else {}
    custom = str(context.get("artifactWriteDiscipline") or "").strip()
    lines = [
        "",
        "Artifact Write Discipline:",
        "- Use `write_native_file` for content-bearing project files, including Markdown, source code, Spec-derived artifacts, SKILL.md, and references/** documents.",
        "- Do NOT use `run_system_command`, shell redirection, `echo`, `New-Item`, `Set-Content`, or `Out-File` to create or populate artifact content files.",
        "- `run_system_command` is allowed for directory creation, listing, and verification commands only; it is not an artifact authoring tool.",
        "- Empty placeholder files, one-line stubs, or files without required source markers do not satisfy the acceptance contract.",
        "- If facts or source evidence are missing, return a blocker/degraded result or request the needed research; do not write blank or invented files.",
    ]
    if custom:
        lines.append(f"- Task-specific note: {custom}")
    return lines


def _format_delegated_plan_context(task_brief: dict | None, planner_context: dict | None) -> str:
    if not isinstance(task_brief, dict) and not isinstance(planner_context, dict):
        return ""
    lines: list[str] = [
        "",
        "<delegated_task_plan>",
        "You are executing one bounded task from the supervisor's planner/delegation pipeline.",
        "Use this local task contract as the routing truth; do not reinterpret the original user request as your primary scope.",
    ]
    if isinstance(planner_context, dict) and planner_context:
        if planner_context.get("planId"):
            lines.append(f"Plan ID: {planner_context.get('planId')}")
        if planner_context.get("executionStrategy"):
            lines.append(f"Execution Strategy: {planner_context.get('executionStrategy')}")
        if planner_context.get("planSummary"):
            lines.append(f"Plan Summary: {planner_context.get('planSummary')}")
        if planner_context.get("taskCount"):
            lines.append(f"Task Count: {planner_context.get('taskCount')}")
        risk_flags = _compact_prompt_value(planner_context.get("riskFlags"))
        if risk_flags:
            lines.append(f"Risk Flags: {risk_flags}")
        dependencies = _compact_prompt_value(planner_context.get("dependencies"))
        if dependencies:
            lines.append(f"Dependencies: {dependencies}")
        global_acceptance = _compact_prompt_value(planner_context.get("globalAcceptanceContract"))
        if global_acceptance:
            lines.append(f"Global Acceptance Contract: {global_acceptance}")
    if isinstance(task_brief, dict) and task_brief:
        lines.append("")
        lines.append("Assigned Task Brief:")
        for label, key in (
            ("Task Brief ID", "taskBriefId"),
            ("Goal", "goal"),
            ("Context", "context"),
            ("Write Set", "writeSet"),
            ("Expected Outputs", "expectedOutputs"),
            ("Behavior Scope", "behaviorScope"),
            ("Required Capabilities", "requiredCapabilities"),
            ("Runtime Access", "runtimeAccess"),
            ("Dependency", "dependency"),
            ("Parallel Group", "parallelGroup"),
            ("Execution Lane Hint", "executionLaneHint"),
            ("Acceptance Contract", "acceptanceContract"),
        ):
            rendered = _compact_prompt_value(task_brief.get(key))
            if rendered:
                lines.append(f"- {label}: {rendered}")
        context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
        writing_brief = context.get("writingExecutionBrief") if isinstance(context.get("writingExecutionBrief"), dict) else {}
        if writing_brief:
            skill = writing_brief.get("skill") if isinstance(writing_brief.get("skill"), dict) else {}
            authorized_refs = writing_brief.get("authorizedRefs") if isinstance(writing_brief.get("authorizedRefs"), dict) else {}
            first_action = str(writing_brief.get("subagentFirstAction") or skill.get("firstActionRequired") or "").strip()
            skill_name = str(skill.get("idOrName") or skill.get("name") or "").strip()
            lines.append("")
            lines.append("Writing Execution Brief:")
            if skill_name:
                lines.append(f"- Skill Name: {skill_name}")
            if skill.get("selectionReason"):
                lines.append(f"- Skill Selection Reason: {_compact_prompt_value(skill.get('selectionReason'))}")
            if first_action == "fetch_skill_instructions":
                if skill_name:
                    lines.append(f"- First Action: You MUST call fetch_skill_instructions(skill_name={skill_name!r}) before drafting or editing.")
                else:
                    lines.append("- First Action: You MUST call fetch_skill_instructions with the exact delegated skill name before drafting; ask the supervisor if the skill name is missing.")
            required_reads = writing_brief.get("requiredInstructionReads") if isinstance(writing_brief.get("requiredInstructionReads"), list) else []
            if required_reads:
                lines.append("- Required Skill Reads: complete these exact fetch_skill_instructions calls before drafting, editing, or writing artifacts. Do not replace them with read_native_file or memory.")
                for item in required_reads[:8]:
                    if not isinstance(item, dict):
                        continue
                    read_skill = str(item.get("skillName") or item.get("name") or skill_name or "").strip()
                    detail_level = str(item.get("detailLevel") or item.get("detail_level") or "").strip()
                    relative_path = str(item.get("relativePath") or item.get("relative_path") or "").strip()
                    reason = _compact_prompt_value(item.get("reason"))
                    args = [f"skill_name={read_skill!r}"] if read_skill else ["skill_name=<delegated skill name>"]
                    if relative_path:
                        args.append(f"relative_path={relative_path!r}")
                    elif detail_level:
                        args.append(f"detail_level={detail_level!r}")
                    lines.append(f"  - fetch_skill_instructions({', '.join(args)})" + (f" — {reason}" if reason else ""))
            if authorized_refs:
                lines.append(f"- Authorized Refs Only: {_compact_prompt_value(authorized_refs)}")
            forbidden = _compact_prompt_value(writing_brief.get("forbiddenInventions"))
            if forbidden:
                lines.append(f"- Forbidden Inventions: {forbidden}")
            acceptance = _compact_prompt_value(writing_brief.get("acceptanceCriteria"))
            if acceptance:
                lines.append(f"- Writing Acceptance: {acceptance}")
            lines.append("- Missing facts must be labeled as assumptions or blockers; do not invent memory, sources, files, tests, or user preferences.")
        lines.extend(_artifact_write_discipline_lines(task_brief))
        capsule = task_brief.get("engineeringTaskCapsule") if isinstance(task_brief.get("engineeringTaskCapsule"), dict) else {}
        capsule_lane = str(
            (capsule or {}).get("runtimeLane")
            or task_brief.get("familyHint")
            or task_brief.get("executionLaneHint")
            or ""
        ).strip().lower()
        engineering_like_capsule = not capsule_lane or "engineer" in capsule_lane or capsule_lane == "verification"
        role = infer_engineering_task_role(task_brief) if engineering_like_capsule else ""
        if capsule or role:
            lines.append("")
            lines.append("Engineering Task Capsule:" if engineering_like_capsule else "Runtime Task Capsule:")
            if capsule_lane and not engineering_like_capsule:
                lines.append(f"- Runtime Lane: {capsule_lane}")
            if role:
                lines.append(f"- Engineering Role: {role}")
            for label, key in (
                ("Critical Files", "criticalFiles"),
                ("Read Set", "readSet"),
                ("Write Set", "writeSet"),
                ("Verification Contract", "verificationContract"),
                ("Proof Expectations", "proofExpectations"),
                ("Risk Flags", "riskFlags"),
            ):
                value = capsule.get(key) if capsule else task_brief.get(key)
                rendered = _compact_prompt_value(value)
                if rendered:
                    lines.append(f"- {label}: {rendered}")
            if role in {"review", "verification"} and not _compact_prompt_value(task_brief.get("writeSet")):
                lines.append("- Write Discipline: Treat this task as read-only. Do not modify production files unless the supervisor explicitly grants a writeSet.")
            elif not _compact_prompt_value(task_brief.get("writeSet")) and role:
                lines.append("- Write Discipline: writeSet is missing. Ask for clarification before editing files.")
    lines.append("</delegated_task_plan>")
    return "\n".join(lines) + "\n"


_INTERACTIVE_CLI_RULE = (
    "[Interactive CLI Rule]\n"
    "Use `run_system_command` only for short synchronous commands.\n"
    "Use `run_system_command(mode=auto)` as the default shell entry; it returns compact final results for short commands and starts a recoverable command session for long-running commands, interactive CLIs/REPLs, and dev servers.\n"
    "Before scaffolding, dependency installation, or bulk writing in a non-empty Active Workspace Root, call `workspace_broker(mode=\"inspect\")` and choose whether to continue an existing project, create a clearly named subdirectory, or ask the user.\n"
    "When a command prompt waits for confirmation, `command_session_broker(mode=\"input\", input_text=\"y\")` submits Enter by default; use `submit=false` only for TUI raw typing.\n"
    "Treat command stdout/stderr/exit code as the primary truth. Broker status is only for waiting input, timeout, backgrounding, or recovery; use `debug=true` only for raw terminal diagnostics.\n"
    "If terminal automation or observation is uncertain, report that uncertainty instead of inventing progress.\n\n"
    "When you have fully completed your assigned task, respond with your findings or status to return control to the supervisor."
)


def _agent_prompt_part(source: str, segment_type: str, text: str, *, scope: str = "") -> dict[str, str]:
    return {"source": source, "type": segment_type, "text": text or "", "scope": scope}


def _split_agent_env_context_parts(env_context: str) -> list[dict[str, str]]:
    text = str(env_context or "")
    if not text:
        return []
    dynamic_prefixes = {
        "Current Time:": "current_time",
        "Host Load:": "host_load",
        "Host Alerts:": "host_alerts",
    }
    parts: list[dict[str, str]] = []
    static_buffer: list[str] = []

    def _flush_static() -> None:
        if not static_buffer:
            return
        parts.append(
            _agent_prompt_part(
                "subagent.environment.static",
                "scoped_static",
                "".join(static_buffer),
                scope="environment",
            )
        )
        static_buffer.clear()

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        dynamic_name = next((name for prefix, name in dynamic_prefixes.items() if stripped.startswith(prefix)), "")
        if dynamic_name:
            _flush_static()
            parts.append(_agent_prompt_part(f"subagent.environment.{dynamic_name}", "dynamic", line, scope="environment"))
        else:
            static_buffer.append(line)
    _flush_static()
    return parts


def _build_agent_system_bundle(
    *,
    agent_name: str,
    agent_system_prompt: str,
    env_context: str,
    active_plan_context: str = "",
    delegated_plan_context: str = "",
    route_prompt_addition: str = "",
) -> dict[str, object]:
    parts: list[dict[str, str]] = [
        _agent_prompt_part(
            "subagent.persona_mission_contracts",
            "stable_static",
            f"<system_persona>\nYou are a specialized agent named {agent_name}.\n{agent_system_prompt}\n</system_persona>\n\n",
            scope="persona",
        ),
        *_split_agent_env_context_parts(env_context),
        _agent_prompt_part("subagent.active_plan", "dynamic", active_plan_context, scope="planner"),
        _agent_prompt_part("subagent.delegated_task_brief", "dynamic", delegated_plan_context, scope="task_brief"),
        _agent_prompt_part("subagent.route_additions", "dynamic", route_prompt_addition, scope="extensions"),
        _agent_prompt_part("subagent.separator", "dynamic", "\n\n", scope="separator"),
        _agent_prompt_part("subagent.interactive_cli_rule", "stable_static", _INTERACTIVE_CLI_RULE, scope="execution_hints"),
    ]
    return {
        "content": "".join(part.get("text") or "" for part in parts),
        "segments": build_prompt_segments_from_parts(parts),
    }


def _build_agent_system_content(
    *,
    agent_name: str,
    agent_system_prompt: str,
    env_context: str,
    active_plan_context: str = "",
    delegated_plan_context: str = "",
    route_prompt_addition: str = "",
) -> str:
    return str(
        _build_agent_system_bundle(
            agent_name=agent_name,
            agent_system_prompt=agent_system_prompt,
            env_context=env_context,
            active_plan_context=active_plan_context,
            delegated_plan_context=delegated_plan_context,
            route_prompt_addition=route_prompt_addition,
        )["content"]
    )


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
    all_native_tools: list | None = None,
    static_extra_tools: list | None = None,
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
        task_brief = route_context.get("taskBrief") if isinstance(route_context.get("taskBrief"), dict) else {}
        runtime_access = normalize_runtime_access((task_brief or {}).get("runtimeAccess"))
        actor_base_tools = filter_visible_tools_for_actor(
            list(all_native_tools or base_tools or []),
            actor="subagent",
            runtime_access=runtime_access,
        )
        tools = _dedupe_tools(actor_base_tools + list(static_extra_tools or []) + selected_mcp_tools + selected_plugin_host_tools)
        routed = create_routed_tool_node(tools, name=name, fallback_goto=fallback_goto)
        return await routed(state)

    return contextual_tool_node


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
        llm_factory.create_chat_model(agent_model_id, streaming=False, timeout=180)
        if agent_model_id
        else default_agent_llm
    )

    def agent_node_func(state):
        try:
            messages = state["messages"]

            workspace_binding = _resolved_workspace_binding_for_state(state)
            workspace_path = str(workspace_binding.get("activeWorkspaceRoot") or workspace_binding.get("mainWorkspaceRoot") or "")
            main_workspace_path = str(workspace_binding.get("mainWorkspaceRoot") or "")
            os_name = platform.system()
            current_time = utc_now_iso()
            host_alerts_line = render_host_alerts_line()
            host_alerts_context = f"{host_alerts_line}\n" if host_alerts_line else ""
            env_context = (
                f"<environment>\n"
                f"OS: {os_name}\n"
                f"Current Time: {current_time}\n"
                f"{render_host_load_line()}\n"
                f"{host_alerts_context}"
                f"Active Workspace Root: {workspace_path}\n"
                f"Main V8 Workspace Store: {main_workspace_path}\n"
                "The Active Workspace Root is the execution boundary for delegated project work; keep command cwd and file writes inside it unless the supervisor explicitly grants another root.\n"
                f"When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, you MUST save them under the Active Workspace Root above.\n"
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
            delegated_runtime_access = normalize_runtime_access((delegated_task_brief or {}).get("runtimeAccess"))
            delegated_planner_context = inherited_route_context.get("plannerContext") if isinstance(inherited_route_context.get("plannerContext"), dict) else None
            delegated_plan_context = _format_delegated_plan_context(delegated_task_brief, delegated_planner_context)
            inherited_query = str(inherited_route_context.get("query") or delegated_query).strip() or delegated_query
            full_task_brief_query = task_brief_query_text(delegated_task_brief)
            extensions_route_query = task_brief_route_query_text(delegated_task_brief)
            delegated_query = full_task_brief_query or inherited_query
            extensions_route_query = extensions_route_query or delegated_query
            contextual_base_tools = _dedupe_tools(
                filter_visible_tools_for_actor(
                    _select_contextual_subagent_native_tools(filtered_native_tools, delegated_runtime_access) + [fetch_skill_instructions_tool],
                    actor="subagent",
                    runtime_access=delegated_runtime_access,
                )
            )
            explicit_base_tools = _dedupe_tools(
                filter_visible_tools_for_actor(
                    list(filtered_native_tools) + [fetch_skill_instructions_tool],
                    actor="subagent",
                    runtime_access=delegated_runtime_access,
                )
            )
            selected_mcp_tools = _resolve_selected_mcp_tools(all_mcp_tools, agent_tool_selectors)
            selected_plugin_host_tools = _resolve_selected_plugin_host_tools(all_plugin_host_tools, agent_tool_selectors)
            explicit_skill_ids, explicit_skill_names = _resolve_selected_skills(agent_tool_selectors, state=state)

            if agent_tool_mode == "explicit":
                base_tools = explicit_base_tools
                available_tools = _dedupe_tools(base_tools + selected_mcp_tools + selected_plugin_host_tools)
                inherited_skill_ids: list[str] = explicit_skill_ids
                inherited_skill_names: list[str] = explicit_skill_names
            else:
                base_tools = contextual_base_tools
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
                    user_query=extensions_route_query,
                    available_tools=available_tools,
                    loaded_agents=None,
                    inherited_skill_ids=inherited_skill_ids,
                    inherited_skill_names=inherited_skill_names,
                    mcp_limit=6,
                    plugin_host_limit=6,
                )
                extensions_runtime_service.emit_route_selected(user_query=extensions_route_query, route_bundle=route_bundle)
                combined_tools = route_bundle.filtered_tools
            finally:
                extensions_runtime_service.reset_execution_context(route_context_token)

            system_bundle = _build_agent_system_bundle(
                agent_name=agent_name,
                agent_system_prompt=agent_system_prompt,
                env_context=env_context,
                active_plan_context=active_plan_context,
                delegated_plan_context=delegated_plan_context,
                route_prompt_addition=route_bundle.prompt_addition,
            )
            sys_msg = SystemMessage(
                content=str(system_bundle["content"]),
                additional_kwargs={"v8_prompt_segments": list(system_bundle.get("segments") or [])},
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
                    query=extensions_route_query,
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
                    planner_context=delegated_planner_context,
                ),
                "toolMode": agent_tool_mode,
                "inheritedSkillIds": inherited_skill_ids,
                "inheritedSkillNames": inherited_skill_names,
                "delegatedFullQuery": delegated_query,
                "extensionsRouteQuery": extensions_route_query,
            }
            for key in ("parentDelegationId", "delegationId", "delegationDepth", "delegationNodeCount", "delegationBudget"):
                if key in inherited_route_context:
                    route_context_record[key] = inherited_route_context.get(key)

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
                        streaming=False,
                        timeout=180,
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
        llm_factory.create_chat_model(agent_model_id, streaming=False, timeout=180)
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
            review_material = "\n\n".join(
                f"[{getattr(message, 'type', 'message')}]\n{str(getattr(message, 'content', '') or '')}"
                for message in messages
                if not isinstance(message, SystemMessage)
            )
            prepared_context = prepare_background_model_messages(
                system_prompt=str(reviewer_sys.content),
                instruction=(
                    "Review the prepared subagent execution transcript. "
                    "Return APPROVE or concise feedback only."
                ),
                materials=[
                    {
                        "title": f"{agent_name} execution transcript",
                        "kind": "subagent_reviewer_context",
                        "content": review_material,
                    }
                ],
                runtime_kind=str(get_runtime_context().get("runtime_kind") or "chat"),
                target_role=f"reviewer:{agent_id}",
                resolved_model_id=agent_model_id or supervisor_model_id,
                component="graph",
                node=f"{agent_id}_reviewer",
            )
            run_messages = [ensure_reasoning_content(m) for m in prepared_context.messages]
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
                    streaming=False,
                    timeout=180,
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

        contextual_base_tools = _dedupe_tools(
            filter_visible_tools_for_actor(
                select_baseline_system_tools(filtered_native_tools) + [fetch_skill_instructions],
                actor="subagent",
                runtime_access=[],
            )
        )
        if agent_tool_mode == "contextual_auto":
            # Contextual agents receive external MCP/PluginHost tools only after the
            # delegated task route selects them. Keep the static tool node narrow so
            # the runtime surface does not silently retain the full external tree.
            tool_node_tools = contextual_base_tools
            tool_node_func = build_contextual_auto_tool_node(
                base_tools=contextual_base_tools,
                all_native_tools=list(filtered_native_tools) + [fetch_skill_instructions],
                static_extra_tools=[],
                all_mcp_tools=all_mcp_tools,
                all_plugin_host_tools=all_plugin_host_tools,
                name=f"{agent_id}_tools",
                fallback_goto=agent_id,
            )
        else:
            tool_node_tools = _dedupe_tools(
                filter_visible_tools_for_actor(
                    list(filtered_native_tools) + [fetch_skill_instructions],
                    actor="subagent",
                    runtime_access=[],
                )
                + _resolve_selected_mcp_tools(all_mcp_tools, tool_selectors)
                + _resolve_selected_plugin_host_tools(all_plugin_host_tools, tool_selectors)
            )
            tool_node_func = build_contextual_auto_tool_node(
                base_tools=tool_node_tools,
                all_native_tools=list(filtered_native_tools) + [fetch_skill_instructions],
                static_extra_tools=_resolve_selected_mcp_tools(all_mcp_tools, tool_selectors)
                + _resolve_selected_plugin_host_tools(all_plugin_host_tools, tool_selectors),
                all_mcp_tools=_resolve_selected_mcp_tools(all_mcp_tools, tool_selectors),
                all_plugin_host_tools=_resolve_selected_plugin_host_tools(all_plugin_host_tools, tool_selectors),
                name=f"{agent_id}_tools",
                fallback_goto=agent_id,
            ) if tool_node_tools else None

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

    return agent_nodes_map
