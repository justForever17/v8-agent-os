import json
import logging
import platform
import re
import uuid
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command

from core.context.delegation import build_delegation_context, latest_delegation_context
from core.background_context_guard import prepare_background_model_messages
from core.delegation_broker import (
    infer_engineering_task_role,
    task_brief_query_text,
    task_brief_requires_child_delegation,
    task_brief_route_query_text,
)
from core.engineering_capsule import engineering_tool_allowed
from core.engineering_kernel import build_engineering_kernel_context, detect_command_environment
from core.context_governance import emit_context_prepared_event
from core.context_orchestrator import context_orchestrator
from core.delegated_agent_charter import DELEGATED_AGENT_OPERATING_CHARTER
from core.host_load import render_host_load_line
from core.safety_active_defense import render_host_alerts_line
from core.prompt_cache_segments import build_prompt_segments_from_parts
from core.runtime.extensions_runtime import ExtensionRouteBundle, extensions_runtime_service
from core.models.factory import llm_factory
from core.response_normalizer import ensure_reasoning_content, extract_text_and_reasoning
from core.system_tools.baseline import select_baseline_system_tool_names, select_baseline_system_tools
from core.runtime_tool_access import (
    filter_visible_tools_for_actor,
    normalize_runtime_access,
    resolve_subagent_runtime_access,
    runtime_tool_names_for_groups,
)
from core.time_truth import utc_now_iso
from core.workspace_capability import build_workspace_binding
from erc.runtime_context import bind_runtime_context, build_runtime_callback_config, get_runtime_context
from .tool_routing import create_routed_tool_node
from .route_context import merge_route_context


def subagent_model_kwargs(model_id: str | None) -> dict[str, int]:
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        return {}
    max_output_tokens = llm_factory.get_model_max_output_tokens(normalized_model_id)
    return {"max_tokens": int(max_output_tokens)} if max_output_tokens else {}


def create_subagent_chat_model(
    model_id: str,
    *,
    role: str,
    **kwargs: Any,
):
    normalized_model_id = str(model_id or "").strip()
    if not normalized_model_id:
        raise ValueError("Subagent model_id must be provided")
    model_kwargs = dict(kwargs)
    model_kwargs.pop("max_tokens", None)
    # Registered workers and their reviewers are user-visible subagent work.
    # Keep this invariant here so a caller cannot silently drop their
    # canonical text/reasoning stream by passing a stale non-streaming flag.
    model_kwargs["streaming"] = True
    model_kwargs.update(subagent_model_kwargs(normalized_model_id))
    return llm_factory.create_chat_model(
        normalized_model_id,
        _role=role,
        **model_kwargs,
    )


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
    excluded = {"delegation_broker", "config_broker", "mcp_server_config"}
    return [
        tool_ref
        for tool_ref in list(tools or [])
        if str(getattr(tool_ref, "name", "")).strip() not in excluded
    ]


def _mark_delegated_message_owner(message: Any, *, agent_id: str, delegation_id: str = "") -> Any:
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    additional_kwargs.update(
        {
            "v8_owner_runtime_kind": "subagent",
            "v8_owner_agent_kind": "subagent",
            "v8_owner_agent_id": agent_id,
            "v8_owner_subagent_id": agent_id,
        }
    )
    if delegation_id:
        additional_kwargs["v8_owner_delegation_id"] = delegation_id
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        return model_copy(update={"additional_kwargs": additional_kwargs})
    message.additional_kwargs = additional_kwargs
    return message


def _delegated_message_owner_id(message: Any) -> str:
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    return str(
        additional_kwargs.get("v8_owner_agent_id")
        or additional_kwargs.get("v8_owner_subagent_id")
        or ""
    ).strip()


def _delegated_tool_names(messages: list[Any], *, agent_id: str) -> list[str]:
    names: set[str] = set()
    for message in list(messages or []):
        if _delegated_message_owner_id(message) != agent_id:
            continue
        if not isinstance(message, AIMessage):
            continue
        for tool_call in list(getattr(message, "tool_calls", None) or []):
            if not isinstance(tool_call, dict):
                continue
            name = str(tool_call.get("name") or "").strip()
            if name:
                names.add(name)
    return sorted(names)


def _delegated_result_text(messages: list[Any], *, agent_id: str) -> str:
    for message in reversed(list(messages or [])):
        if _delegated_message_owner_id(message) != agent_id:
            continue
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        exact_result = additional_kwargs.get("v8_subagent_result_text")
        if exact_result is not None:
            return str(exact_result).strip()
        if isinstance(message, AIMessage):
            content = getattr(message, "content", "")
            return content.strip() if isinstance(content, str) else str(content).strip()
    return ""


def _delegated_visible_result_text(response: Any) -> str:
    final_content, _reasoning = extract_text_and_reasoning(response)
    if final_content:
        return final_content.strip()
    raw_content = getattr(response, "content", response)
    return (raw_content if isinstance(raw_content, str) else str(raw_content)).strip()


def _bounded_delegated_task_messages(messages: list[Any], task_brief: dict[str, Any] | None) -> list[Any]:
    if not isinstance(task_brief, dict) or not task_brief:
        return list(messages or [])
    source_messages = list(messages or [])
    marked_index = -1
    for index, message in enumerate(source_messages):
        if not isinstance(message, HumanMessage):
            continue
        additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        if str(additional_kwargs.get("v8_governance_type") or "").strip() == "delegated_task_instruction":
            marked_index = index
    if marked_index >= 0:
        # Preserve the complete current delegated branch. In particular, the
        # next agent turn must see its own AI tool call and the corresponding
        # ToolMessage; dropping either makes the worker restart the task and
        # can trigger an infinite write/read loop. Cross-task history remains
        # excluded by anchoring at the latest delegated instruction, while the
        # shared context orchestrator remains responsible for model-window
        # compaction.
        return source_messages[marked_index:]
    query = task_brief_query_text(task_brief) or str(task_brief.get("goal") or "").strip()
    return [
        HumanMessage(
            content=f"[Supervisor Delegated Task]\n{query}",
            additional_kwargs={
                "v8_governance_type": "delegated_task_instruction",
                "v8_task_brief_id": str(task_brief.get("taskBriefId") or "").strip(),
            },
        )
    ]


def _select_contextual_subagent_native_tools(filtered_native_tools: list, runtime_access: list[str]) -> list:
    """Keep contextual_auto narrow while retaining collaboration controls."""
    baseline_tools = list(select_baseline_system_tools(filtered_native_tools))
    collaboration_tools = [
        tool_ref
        for tool_ref in list(filtered_native_tools or [])
        if str(getattr(tool_ref, "name", "") or "").strip() in {"delegation_broker", "plugin_broker"}
    ]
    granted_runtime_tool_names = runtime_tool_names_for_groups(runtime_access)
    granted_runtime_tools = [
        tool_ref
        for tool_ref in list(filtered_native_tools or [])
        if str(getattr(tool_ref, "name", "")).strip() in granted_runtime_tool_names
    ]
    return _dedupe_tools(baseline_tools + collaboration_tools + granted_runtime_tools)


def _resolve_delegated_runtime_access(
    agent_data: dict[str, Any] | None,
    requested_runtime_access: Any,
    *,
    delegation_depth: Any,
) -> list[str]:
    """Resolve direct-child bindings without re-expanding a terminal grandchild."""

    try:
        depth = max(0, int(delegation_depth or 0))
    except (TypeError, ValueError):
        depth = 0
    requested = normalize_runtime_access(requested_runtime_access)
    if depth >= 2:
        return requested
    return resolve_subagent_runtime_access(agent_data, requested)


def _apply_task_tool_policy(tools: list, task_brief: dict[str, Any] | None) -> list:
    task_brief = dict(task_brief or {})
    policy = task_brief.get("toolPolicy") if isinstance(task_brief.get("toolPolicy"), dict) else {}
    mode = str(policy.get("mode") or "default").strip().lower()
    allowed = {
        str(item or "").strip()
        for item in list(policy.get("allowedTools") or task_brief.get("allowedTools") or [])
        if str(item or "").strip()
    }
    forbidden = {
        str(item or "").strip()
        for item in list(policy.get("forbiddenTools") or task_brief.get("forbiddenTools") or [])
        if str(item or "").strip()
    }
    if mode == "none":
        return []

    def _names(tool_ref: Any) -> set[str]:
        return {
            value
            for value in (
                str(getattr(tool_ref, "name", "") or "").strip(),
                _canonical_tool_name(tool_ref),
                _raw_tool_name(tool_ref),
            )
            if value
        }

    filtered: list[Any] = []
    for tool_ref in list(tools or []):
        names = _names(tool_ref)
        if not all(engineering_tool_allowed(name, task_brief) for name in names):
            continue
        if names & forbidden:
            continue
        if mode == "allowlist" and not (names & allowed):
            continue
        filtered.append(tool_ref)
    return _dedupe_tools(filtered)


def _align_extension_route_to_task_tools(route_bundle: Any, tools: list[Any]) -> Any:
    """Make the route prompt and audit projection match the final tool surface."""

    final_tool_names = {
        str(getattr(tool, "name", "") or "").strip()
        for tool in list(tools or [])
        if str(getattr(tool, "name", "") or "").strip()
    }
    skill_available = "fetch_skill_instructions" in final_tool_names
    original_mcp_names = [
        str(item or "").strip()
        for item in list(getattr(route_bundle, "exposed_mcp_tool_names", None) or [])
        if str(item or "").strip()
    ]
    exposed_mcp_names = [name for name in original_mcp_names if name in final_tool_names]
    selected_skill_names = (
        list(getattr(route_bundle, "selected_skill_names", None) or [])
        if skill_available
        else []
    )
    selected_skill_ids = (
        list(getattr(route_bundle, "selected_skill_ids", None) or [])
        if skill_available
        else []
    )
    route_unchanged = skill_available and exposed_mcp_names == original_mcp_names
    route_bundle.filtered_tools = list(tools or [])
    route_bundle.selected_skill_names = selected_skill_names
    route_bundle.selected_skill_ids = selected_skill_ids
    route_bundle.exposed_mcp_tool_names = exposed_mcp_names
    if route_unchanged:
        return route_bundle

    candidate_summary = dict(getattr(route_bundle, "candidate_summary", None) or {})
    candidate_summary["selectedSkills"] = list(selected_skill_names)
    candidate_summary["selectedSkillIds"] = list(selected_skill_ids)
    candidate_summary["selectedMcpTools"] = list(exposed_mcp_names)
    if not skill_available:
        candidate_summary["skillEntries"] = []
    route_bundle.candidate_summary = candidate_summary
    prompt_lines = [
        "\n[Extensions Runtime]",
        "- The delegated task tool policy is authoritative for this worker.",
        "- Extension candidates are optional references, not mandatory tool-use instructions.",
    ]
    if selected_skill_names:
        prompt_lines.append(f"- Selected Skill entries exposed: {', '.join(selected_skill_names)}")
    else:
        prompt_lines.append("- No Skill entry or fetch_skill_instructions tool is exposed for this task.")
    if exposed_mcp_names:
        prompt_lines.append(f"- MCP tools exposed: {', '.join(exposed_mcp_names)}")
    else:
        prompt_lines.append("- No MCP tool is exposed for this task.")
    if "plugin_cli" in final_tool_names:
        prompt_lines.append("- plugin_cli remains available only through its typed action contract.")
    prompt_lines.append("[/Extensions Runtime]")
    route_bundle.prompt_addition = "\n".join(prompt_lines)
    return route_bundle


def _preserve_direct_worker_extension_candidates(
    route_bundle: Any,
    tools: list[Any],
    task_brief: dict[str, Any] | None,
    *,
    delegation_depth: int,
) -> list[Any]:
    """Keep optional extension candidates for direct workers only.

    A task allowlist bounds native execution authority. It must not silently
    erase the Skill/MCP shortlist that helps a direct subagent choose a better
    method. Depth-two disposable workers skip extension prefiltering because
    they execute one already-routed atomic shard. Explicit ``none`` and
    forbidden tool entries still win.
    """

    if int(delegation_depth or 0) >= 2:
        return _dedupe_tools(list(tools or []))
    task = dict(task_brief or {})
    policy = dict(task.get("toolPolicy") or {}) if isinstance(task.get("toolPolicy"), dict) else {}
    if str(policy.get("mode") or "").strip().lower() == "none":
        return _dedupe_tools(list(tools or []))
    forbidden = {
        str(item or "").strip()
        for item in list(policy.get("forbiddenTools") or task.get("forbiddenTools") or [])
        if str(item or "").strip()
    }
    optional_names = {
        "fetch_skill_instructions",
        *(
            str(item or "").strip()
            for item in list(getattr(route_bundle, "exposed_mcp_tool_names", None) or [])
            if str(item or "").strip()
        ),
    }
    restored = list(tools or [])
    for tool_ref in list(getattr(route_bundle, "filtered_tools", None) or []):
        names = {
            value
            for value in (
                str(getattr(tool_ref, "name", "") or "").strip(),
                _canonical_tool_name(tool_ref),
                _raw_tool_name(tool_ref),
            )
            if value
        }
        if not names.intersection(optional_names) or names.intersection(forbidden):
            continue
        restored.append(tool_ref)
    return _dedupe_tools(restored)


def _build_atomic_worker_extension_route(tools: list[Any]) -> ExtensionRouteBundle:
    """Return the direct task tool surface used by terminal delegated workers.

    A disposable grandchild is an atomic execution unit. Running the normal
    Extensions prefilter would repeat the parent's routing work and add
    unrelated suggestions. The baseline and task-authorized tools remain
    available for the worker to select by relevance.
    """

    return ExtensionRouteBundle(
        prompt_addition="",
        filtered_tools=_dedupe_tools(list(tools or [])),
        selected_skill_names=[],
        selected_skill_ids=[],
        skill_root_descriptors=[],
        exposed_mcp_tool_names=[],
        candidate_summary={
            "mode": "atomic_task_direct",
            "routingMode": "atomic_task_direct",
            "skillsRoutingMode": "disabled_for_atomic_worker",
            "mcpRoutingMode": "disabled_for_atomic_worker",
            "selectedSkills": [],
            "selectedSkillIds": [],
            "selectedMcpTools": [],
            "skillEntries": [],
        },
    )


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
        }
    return {
        "query": "",
        "selectedSkillIds": [],
        "selectedSkillNames": [],
        "selectedSkillEntries": [],
        "skillRootDescriptors": [],
        "selectedMcpTools": [],
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


def _stable_prompt_json_value(value):
    if isinstance(value, dict):
        return {
            str(key): _stable_prompt_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_prompt_json_value(item) for item in value]
    if isinstance(value, set):
        normalized = [_stable_prompt_json_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _compact_prompt_value(value) -> str:
    if isinstance(value, dict):
        return json.dumps(
            _stable_prompt_json_value(value),
            ensure_ascii=False,
            sort_keys=True,
        )
    if isinstance(value, (list, tuple, set)):
        items = sorted(value, key=str) if isinstance(value, set) else list(value)
        if any(isinstance(item, (dict, list, tuple, set)) for item in items):
            return json.dumps(
                _stable_prompt_json_value(items),
                ensure_ascii=False,
                sort_keys=True,
            )
        return ", ".join(str(item).strip() for item in items if str(item).strip())
    return str(value or "").strip()


_AGENT_SURFACE_SECRET_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "password",
        "passphrase",
        "secret",
        "token",
    }
)
_AGENT_SURFACE_SECRET_EXACT_KEYS = frozenset(
    {
        "auth",
        "authentication",
        "bearer",
        "apikey",
        "apitoken",
        "accesstoken",
        "authtoken",
        "bearertoken",
        "clientsecret",
        "cookiejar",
        "idtoken",
        "privatekey",
        "refreshtoken",
        "sessiontoken",
        "secretkey",
        "authorizationheader",
    }
)


def _agent_surface_key_parts(value: Any) -> list[str]:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    return [part.lower() for part in re.findall(r"[A-Za-z0-9]+", separated)]


def _agent_surface_secret_key(value: Any) -> bool:
    parts = _agent_surface_key_parts(value)
    normalized = "".join(parts)
    if normalized in _AGENT_SURFACE_SECRET_EXACT_KEYS:
        return True
    if normalized.endswith(
        (
            "apikey",
            "accesstoken",
            "refreshtoken",
            "privatekey",
            "clientsecret",
            "secretkey",
        )
    ):
        return True
    return bool(parts and parts[-1] in _AGENT_SURFACE_SECRET_KEY_PARTS)


def _redact_agent_surface_value(
    value: Any,
    *,
    root_path: str,
) -> tuple[Any, list[str]]:
    redacted_paths: list[str] = []

    def redact(item: Any, *, path: str) -> Any:
        if isinstance(item, dict):
            result: dict[str, Any] = {}
            for key, nested in item.items():
                key_text = str(key)
                child_path = f"{path}.{key_text}" if path else key_text
                if _agent_surface_secret_key(key_text):
                    result[key_text] = "<redacted>"
                    redacted_paths.append(child_path)
                else:
                    result[key_text] = redact(nested, path=child_path)
            return result
        if isinstance(item, (list, tuple)):
            return [
                redact(nested, path=f"{path}[{index}]")
                for index, nested in enumerate(item)
            ]
        if isinstance(item, set):
            return [
                redact(nested, path=f"{path}[{index}]")
                for index, nested in enumerate(sorted(item, key=str))
            ]
        return item

    return redact(value, path=root_path), redacted_paths


def _redact_agent_surface_extensions(value: Any) -> tuple[Any, list[str]]:
    return _redact_agent_surface_value(value, root_path="extensions")


def _prompt_recovery_metadata(value: Any, *, fallback: Any = None) -> dict[str, Any]:
    detail_refs: list[str] = []
    raw_refs: list[str] = []
    recovery_tools: list[str] = []

    def append_unique(target: list[str], nested: Any) -> None:
        if isinstance(nested, dict):
            for child in nested.values():
                append_unique(target, child)
            return
        if isinstance(nested, (list, tuple, set)):
            for child in nested:
                append_unique(target, child)
            return
        text = str(nested or "").strip()
        if text and text not in target:
            target.append(text)

    def collect(nested: Any) -> None:
        if isinstance(nested, dict):
            for key, child in nested.items():
                normalized_key = "".join(_agent_surface_key_parts(key))
                if normalized_key in {"detailref", "detailrefs", "taskdetailref", "taskdetailrefs"}:
                    append_unique(detail_refs, child)
                elif normalized_key in {"rawref", "rawrefs"}:
                    append_unique(raw_refs, child)
                elif normalized_key in {"detailtool", "detailtools", "recoverytool", "recoverytools"}:
                    append_unique(recovery_tools, child)
                else:
                    collect(child)
            return
        if isinstance(nested, (list, tuple, set)):
            for child in nested:
                collect(child)

    collect(value)
    collect(fallback)
    if any(ref.startswith("toolobs://") for ref in raw_refs):
        append_unique(recovery_tools, "tool_observation_detail(rawRef)")
    bounded_detail_refs = [ref[:360] for ref in detail_refs[:8]]
    bounded_raw_refs = [ref[:360] for ref in raw_refs[:8]]
    bounded_tools = [tool[:360] for tool in recovery_tools[:6]]
    metadata: dict[str, Any] = {
        "recoveryRefAvailable": bool(bounded_detail_refs or bounded_raw_refs),
        "recoveryAvailable": bool(
            any(ref.startswith("toolobs://") for ref in bounded_raw_refs)
            or ((bounded_detail_refs or bounded_raw_refs) and bounded_tools)
        ),
    }
    if bounded_detail_refs:
        metadata["detailRef"] = bounded_detail_refs[0]
        if len(bounded_detail_refs) > 1:
            metadata["detailRefs"] = bounded_detail_refs
    if bounded_raw_refs:
        metadata["rawRef"] = bounded_raw_refs[0]
        if len(bounded_raw_refs) > 1:
            metadata["rawRefs"] = bounded_raw_refs
    if bounded_tools:
        metadata["recoveryTools"] = bounded_tools
    metadata["recoveryGuidance"] = (
        "Use only the exact listed ref with its granted authoritative detail tool; never treat a ref as a filesystem path."
        if metadata["recoveryAvailable"]
        else "No independently readable recovery ref/tool pair is present on this Agent Surface. Do not infer omitted content; identify the truncated field to the parent if it affects acceptance."
    )
    return metadata


def _compact_prompt_text(value, *, limit: int = 1600, recovery_source: Any = None) -> str:
    text = _compact_prompt_value(value)
    if len(text) <= limit:
        return text
    metadata = _prompt_recovery_metadata(value, fallback=recovery_source)
    metadata.update(
        {
            "truncated": True,
            "omittedCount": len(text) - limit,
            "omittedUnit": "characters",
        }
    )
    rendered_metadata = json.dumps(
        _stable_prompt_json_value(metadata),
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"{text[:limit].rstrip()} ... [truncation: {rendered_metadata}]"


_AGENT_VISIBLE_CONTEXT_KEYS: tuple[tuple[str, str, int], ...] = (
    ("Source", "source", 320),
    ("Spec ID", "specId", 120),
    ("Task ID", "taskId", 120),
    ("Task Detail Ref", "taskDetailRef", 240),
    ("Task Excerpt", "taskExcerpt", 1800),
    ("Assigned Task IDs", "assignedTaskIds", 500),
    ("Assigned Research Brief", "assignedResearchBrief", 2600),
    ("Detail Refs", "detailRefs", 900),
    ("Evidence Refs", "evidenceRefs", 900),
    ("Task Detail Refs", "taskDetailRefs", 900),
    ("Spec Execution Summary", "specExecutionSummary", 3600),
    ("Framework Digest", "frameworkDigest", 1400),
    ("Approved Requirement Slice", "approvedRequirementSlice", 5200),
    ("Approved Design Slice", "approvedDesignSlice", 5200),
    ("Spec Document Paths", "specDocumentPaths", 900),
    ("Spec Ref Usage", "specRefUsage", 900),
    ("Expected Output", "expectedOutput", 800),
    ("Expected Outputs", "expectedOutputs", 900),
    ("Upstream Dependency Results", "dependencyResults", 7200),
    ("Shared Context", "sharedContext", 3600),
    ("Worker Context", "workerContext", 2600),
    ("Task Context", "notes", 1800),
    ("Upstream Handoffs", "upstreamHandoffs", 7200),
    ("Handoff Usage", "handoffUsage", 900),
    ("Requested Evidence Refs", "requestedEvidenceRefs", 1200),
    ("Unresolved Evidence Refs", "unresolvedEvidenceRefs", 1200),
    ("Evidence Resolution", "evidenceResolutionDiagnostics", 1800),
    ("Shell Dialect", "shellDialect", 120),
    ("Workspace Path", "workspacePath", 260),
    ("Terminal Delegation Role", "terminalDelegationRole", 800),
    ("Verification Evidence Contract", "verificationEvidenceContract", 1800),
    ("Artifact Write Discipline", "artifactWriteDiscipline", 800),
    ("Artifact Acceptance Guard", "artifactAcceptanceGuard", 800),
)

_RUNTIME_ONLY_CONTEXT_KEYS = {
    "stageContent",
    "specExecutionBundle",
    "engineeringExecutionContract",
    "handoffContract",
    "creativeMediaExecutionContract",
    "creative_media_execution_contract",
    "canvasExecutionContract",
    "canvas_execution_contract",
    "assignedTaskDetails",
    "assignedTaskSummaries",
    "parentContext",
}


def _agent_visible_context_lines(context: dict | None) -> list[str]:
    if not isinstance(context, dict) or not context:
        return []
    lines = ["", "Agent-Visible Context:"]
    emitted = False
    shared_recovery_source = {
        key: context.get(key)
        for key in (
            "detailRef",
            "detailRefs",
            "taskDetailRef",
            "taskDetailRefs",
            "rawRef",
            "rawRefs",
            "detailTool",
            "detailTools",
            "recoveryTool",
            "recoveryTools",
        )
        if context.get(key) not in (None, "", [], {})
    }
    for label, key, limit in _AGENT_VISIBLE_CONTEXT_KEYS:
        if key not in context:
            continue
        surface_value, redacted_paths = _redact_agent_surface_value(
            context.get(key),
            root_path=key,
        )
        rendered = _compact_prompt_text(
            surface_value,
            limit=limit,
            recovery_source=shared_recovery_source,
        )
        if rendered:
            lines.append(f"- {label}: {rendered}")
            emitted = True
        if redacted_paths:
            lines.append(
                f"- {label} redactions: "
                + json.dumps(sorted(redacted_paths), ensure_ascii=False)
            )
            emitted = True
    omitted = [key for key in _RUNTIME_ONLY_CONTEXT_KEYS if key in context]
    if omitted:
        lines.append(
            "- Runtime-only metadata was omitted from this prompt. The approved task-specific slices above are authoritative. Traceability refs are not URLs; use listed workspace document paths when a broader approved section is needed."
        )
        emitted = True
    return lines if emitted else []


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
        "- Use `write_native_file` for assigned final project artifacts, including Markdown, source code, SKILL.md, and references/** documents.",
        "- Never author the canonical Spec contract documents (`requirements.md`, `design.md`, or `tasks.md`) with `write_native_file`; those stages belong exclusively to `spec_broker` before runtime execution is approved.",
        "- Do NOT use `run_system_command`, shell redirection, `echo`, `New-Item`, `Set-Content`, or `Out-File` to create or populate artifact content files.",
        "- `run_system_command` is allowed for directory creation, listing, and verification commands only; it is not an artifact authoring tool.",
        "- Empty placeholder files, one-line stubs, or files without required source markers do not satisfy the acceptance contract.",
        "- If facts or source evidence are missing, return a blocker/degraded result or request the needed research; do not write blank or invented files.",
    ]
    if custom:
        lines.append(f"- Task-specific note: {custom}")
    return lines


def _format_collaboration_identity_contract(
    *,
    actor_name: str,
    task_brief: dict | None,
    delegation_depth: int | None = None,
) -> str:
    task = dict(task_brief or {})
    try:
        depth = max(1, int(delegation_depth or task.get("delegationDepth") or 1))
    except (TypeError, ValueError):
        depth = 1
    context = task.get("context") if isinstance(task.get("context"), dict) else {}
    mirror = context.get("ephemeralMirror") if isinstance(context.get("ephemeralMirror"), dict) else {}
    effective_name = str(
        actor_name
        or mirror.get("name")
        or task.get("ephemeralAgentName")
        or "delegated worker"
    ).strip()
    parent_name = str(
        mirror.get("parentAgentName")
        or task.get("ephemeralParentAgentName")
        or ("Supervisor" if depth <= 1 else "direct parent subagent")
    ).strip()

    lines = ["", "<collaboration_identity>"]
    if depth >= 2:
        lines.extend(
            [
                "Structural role: grandchild / terminal delegated worker (delegation depth 2 of 2).",
                f"Runtime identity: {effective_name}. Immediate parent: {parent_name}.",
                "You are an ephemeral execution mirror owned by the immediate parent, not the Supervisor, not a persistent registered Agent, and not a sibling specialist.",
                "Names or specialist roles mentioned in task prose describe desired capability only; they do not override this structural identity.",
                "Complete the exact atomic shard and return concrete evidence to the immediate parent. Do not address the user directly and do not create another delegation layer.",
            ]
        )
    else:
        lines.extend(
            [
                "Structural role: direct subagent (delegation depth 1 of 2).",
                f"Runtime identity: {effective_name}. Immediate parent: Supervisor/runtime coordinator.",
                "You are the registered specialist responsible for this task brief. Keep sibling scopes separate and return a typed handoff to the parent.",
                "When the task materially benefits from one independent shard and delegation_broker is visible, you may create one terminal grandchild layer; you remain responsible for integrating its evidence.",
            ]
        )
    lines.extend(
        [
            "Tool selection: visible tools are a candidate toolbox, not a checklist. Choose the smallest relevant subset that proves the acceptance contract; do not probe unrelated tools or mistake optional Skill/MCP suggestions for mandatory instructions.",
            "If a required capability is genuinely absent, report the concrete missing capability and affected acceptance item instead of changing your identity or broadening the task.",
            "</collaboration_identity>",
            "",
        ]
    )
    return "\n".join(lines)


def _format_delegated_task_contract(task_brief: dict | None) -> str:
    if not isinstance(task_brief, dict):
        return ""
    lines: list[str] = [
        "",
        "<delegated_task_plan>",
        "You are executing one bounded task from the supervisor's delegation/runtime pipeline.",
        "Use this local task contract as the routing truth; do not reinterpret the original user request as your primary scope.",
        "For code, file, command, test, or artifact work: treat taskBriefId, workspace, readSet/writeSet, acceptance, proofExpectations, and any Spec refs as your execution boundary. If required boundaries are missing, return a blocker instead of broadening the task.",
        "If the task is to configure MCP servers, use the supervisor-provided MCP config route/tool; do not call Admin login-only APIs or edit V8OS config files directly.",
    ]
    if isinstance(task_brief, dict) and task_brief:
        agent_surface_extensions, redacted_extension_paths = _redact_agent_surface_extensions(
            task_brief.get("extensions")
        )
        lines.append("")
        lines.append("Assigned Task Brief:")
        for label, key in (
            ("Schema Version", "schemaVersion"),
            ("Task Brief ID", "taskBriefId"),
            ("Goal", "goal"),
            ("Read Set", "readSet"),
            ("Write Set", "writeSet"),
            ("Expected Outputs", "expectedOutputs"),
            ("Expected Artifacts", "expectedArtifacts"),
            ("Constraints", "constraints"),
            ("Behavior Scope", "behaviorScope"),
            ("Evidence Refs", "evidenceRefs"),
            ("Detail Refs", "detailRefs"),
            ("Spec Refs", "specRefs"),
            ("Proof Expectations", "proofExpectations"),
            ("Required Capabilities", "requiredCapabilities"),
            ("Runtime Access", "runtimeAccess"),
            ("Tool Policy", "toolPolicy"),
            ("Side Effect Policy", "sideEffectPolicy"),
            ("Execution Budget", "budget"),
            ("Failure Policy", "failurePolicy"),
            ("Dependencies", "dependencies"),
            ("Extensions", "extensions"),
            ("Unsupported Fields", "unsupportedFields"),
            ("Parallel Group", "parallelGroup"),
            ("Execution Lane Hint", "executionLaneHint"),
            ("Acceptance Contract", "acceptanceContract"),
            ("Acceptance Tiers", "acceptanceTiers"),
        ):
            value = task_brief.get(key)
            if key == "dependencies" and value in (None, "", []):
                value = task_brief.get("dependency")
            elif key == "extensions":
                value = agent_surface_extensions
            rendered = _compact_prompt_value(value)
            if rendered:
                lines.append(f"- {label}: {rendered}")
        if redacted_extension_paths:
            lines.append(
                "- Extensions redaction: sensitive values were replaced with `<redacted>` on the Agent Surface; "
                "their canonical runtime values were not modified. Redacted paths: "
                + ", ".join(redacted_extension_paths[:24])
                + (
                    f"; {len(redacted_extension_paths) - 24} additional path(s) redacted"
                    if len(redacted_extension_paths) > 24
                    else ""
                )
                + "."
            )
        if task_brief.get("unsupportedFields"):
            lines.append(
                "- Extension discipline: unsupported fields are preserved for context and diagnostics only; "
                "they do not grant tools, side effects, workspace writes, or routing authority."
            )
        tool_policy = task_brief.get("toolPolicy") if isinstance(task_brief.get("toolPolicy"), dict) else {}
        tool_policy_mode = str(tool_policy.get("mode") or "default").strip().lower()
        if tool_policy_mode == "none":
            lines.append("- Tool discipline: this task has no tool authority. Return the requested result without probing tools.")
        elif tool_policy_mode == "allowlist":
            lines.append("- Tool discipline: only the explicit allowlist is available; do not report other tools as missing.")
        else:
            lines.append("- Tool discipline: call a granted tool only when it is necessary for this task's acceptance contract; do not probe unrelated capabilities.")
        if bool(task_brief.get("readOnly") or task_brief.get("read_only")):
            lines.append(
                "- Read-only evidence discipline: use the command/file ToolMessage already returned in memory. "
                "Do not redirect output or create temporary evidence, stdout/stderr capture, log, or report files."
            )
        lines.append(
            "- Evidence sufficiency: once prior ToolMessages directly prove an acceptance item, reuse that evidence. Do not recapture the same file or command result through alternate encodings; stop probing and return the typed handoff (or dispatch the one required child) promptly."
        )
        allowed_tool_names = {
            str(item or "").strip()
            for item in list(tool_policy.get("allowedTools") or task_brief.get("allowedTools") or [])
            if str(item or "").strip()
        }
        tool_policy_allows_delegation = (
            tool_policy_mode not in {"none", "allowlist"}
            or (tool_policy_mode == "allowlist" and "delegation_broker" in allowed_tool_names)
        )
        delegation_policy = (
            task_brief.get("delegationPolicy")
            if isinstance(task_brief.get("delegationPolicy"), dict)
            else task_brief.get("delegation_policy")
            if isinstance(task_brief.get("delegation_policy"), dict)
            else {}
        )
        try:
            delegation_depth = max(1, int(task_brief.get("delegationDepth") or 1))
        except (TypeError, ValueError):
            delegation_depth = 1
        child_policy_marker = task_brief.get("childDelegationPolicyExplicit")
        if child_policy_marker is None:
            child_policy_marker = task_brief.get("child_delegation_policy_explicit")
        if child_policy_marker is None:
            child_policy_explicit = any(
                key in task_brief
                for key in ("allowChildDelegation", "allow_child_delegation")
            ) or any(
                key in delegation_policy
                for key in ("allowChildDelegation", "allow_child_delegation")
            )
        else:
            child_policy_explicit = bool(child_policy_marker)
        explicit_child_policy = (
            next(
                (
                    value
                    for value in (
                        task_brief.get("allowChildDelegation"),
                        task_brief.get("allow_child_delegation"),
                        delegation_policy.get("allowChildDelegation"),
                        delegation_policy.get("allow_child_delegation"),
                    )
                    if value is not None
                ),
                None,
            )
            if child_policy_explicit
            else None
        )
        child_delegation_allowed = (
            delegation_depth == 1
            and (True if explicit_child_policy is None else bool(explicit_child_policy))
            and tool_policy_allows_delegation
        )
        child_delegation_required = task_brief_requires_child_delegation(task_brief)
        lines.append("")
        lines.append("Delegation Authority:")
        if child_delegation_allowed:
            lines.append(
                "- You are a direct subagent. `delegation_broker(mode='dispatch')` may create one concurrent layer of disposable mirror workers when independent shards materially speed up this exact task."
            )
            lines.append(
                "- Each grandchild is a temporary, cleaner-context mirror of you, named from your own name plus a worker suffix. Do not select another registered subagent as your grandchild; supply explicit shard boundaries and acceptance, then let the broker preserve lineage. Grandchildren cannot delegate again and are never persisted."
            )
            if child_delegation_required:
                lines.append(
                    "- REQUIRED BY ACCEPTANCE: follow this exact sequence: (1) complete your own assigned write, (2) run your own local self-check, (3) call `delegation_broker(mode='dispatch')` exactly once for the independent verification shard, and (4) wait for its handoff before returning your final result."
                )
                lines.append(
                    "- The disposable mirror rule is authoritative for this depth. If the goal or acceptance prose names a registered Agent such as Verification Engineer as the grandchild, treat that name only as a capability label; do not pass targetAgentName, preferredAgentId, or another registered Agent to the broker."
                )
        else:
            lines.append(
                "- This actor cannot create another delegation layer under the current depth or tool policy. The absence of `delegation_broker` is intentional, not a missing-tool failure."
            )
            lines.append(
                "- Complete the assigned slice with the tools you have, or return a concrete blocker without attempting to create another worker."
            )
            if child_delegation_required:
                lines.append(
                    "- Contract conflict: the must-level acceptance requires a child verifier, but this task has no child-delegation authority. Return `required_child_delegation_unavailable`; never claim completion."
                )
            if delegation_depth >= 2:
                lines.append(
                    "- This is a terminal depth-two shard. Select from the visible tools by relevance, execute the acceptance steps, and return evidence to the immediate parent without requesting another Agent."
                )
        context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
        active_collaborators = [
            item for item in list(context.get("activeCollaborators") or [])
            if isinstance(item, dict)
        ]
        if active_collaborators:
            lines.append("")
            lines.append("Concurrent Collaboration Boundaries:")
            lines.append("- These are reverse-boundary exclusions, not work you should absorb. The current task brief and its writeSet remain your only positive execution authority.")
            lines.append("- Do not create, modify, verify, or run a command whose side effects produce a peer task's outputs. Choose a non-writing check or return a boundary conflict when current acceptance would cross that line.")
            for item in active_collaborators[:12]:
                name = _compact_prompt_value(item.get("name")) or "peer"
                task_id = _compact_prompt_value(item.get("taskBriefId"))
                work_summary = _compact_prompt_value(item.get("workSummary")) or "concurrent delegated work"
                status = _compact_prompt_value(item.get("status"))
                identity = f"{name} [{task_id}]" if task_id else name
                lines.append(f"- EXCLUDED — {identity}: {work_summary}" + (f" ({status})" if status else ""))
        lines.extend(_agent_visible_context_lines(context))
        runtime_owned_execution_contracts = (
            (
                "Creative Media Execution Contract",
                context.get("creativeMediaExecutionContract")
                or context.get("creative_media_execution_contract"),
            ),
            (
                "Validated Canvas Execution Contract",
                context.get("canvasExecutionContract")
                or context.get("canvas_execution_contract"),
            ),
        )
        for label, contract in runtime_owned_execution_contracts:
            if not isinstance(contract, dict) or not contract:
                continue
            lines.append("")
            lines.append(f"Runtime-Owned {label} (authoritative and immutable):")
            lines.append(
                "- Preserve the canonical tool/action/operation, source and output lineage, and "
                "session/workspace/run provenance exactly. Do not infer a replacement from surrounding prose."
            )
            lines.append(
                "- If the contract cannot be executed as written, return `execution_intent_conflict`; "
                "do not compile a replacement recipe or substitute another operation."
            )
            lines.append(f"- Canonical Contract: {_compact_prompt_value(contract)}")
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
        execution_contract = context.get("engineeringExecutionContract") if isinstance(context.get("engineeringExecutionContract"), dict) else {}
        handoff_contract = context.get("handoffContract") if isinstance(context.get("handoffContract"), dict) else {}
        if execution_contract:
            lines.append("")
            lines.append("Engineering Execution Contract:")
            for label, key in (
                ("Task ID", "taskId"),
                ("Workspace", "workspacePath"),
                ("Runtime Family", "runtimeFamily"),
                ("Write Required", "writeRequired"),
                ("Allowed Workset", "allowedWorkset"),
                ("Expected Artifacts", "expectedArtifacts"),
                ("Must Read", "mustRead"),
                ("Acceptance", "acceptance"),
                ("Forbidden Scope", "forbiddenScopes"),
            ):
                rendered = _compact_prompt_value(execution_contract.get(key))
                if rendered:
                    lines.append(f"- {label}: {rendered}")
            source_refs = execution_contract.get("sourceRefs") if isinstance(execution_contract.get("sourceRefs"), dict) else {}
            detail_refs = _compact_prompt_value(source_refs.get("detailRefs"))
            requirement_ids = _compact_prompt_value(source_refs.get("requirementIds") or source_refs.get("taskIds"))
            design_ids = _compact_prompt_value(source_refs.get("designIds"))
            if requirement_ids:
                lines.append(f"- Requirement/Task Refs: {requirement_ids}")
            if design_ids:
                lines.append(f"- Design Refs: {design_ids}")
            if detail_refs:
                lines.append(f"- Detail Refs: {detail_refs}")
        if handoff_contract:
            lines.append("")
            lines.append("Required Typed Handoff:")
            for label, key in (
                ("Type", "type"),
                ("Required Fields", "requiredFields"),
                ("Must Include", "mustInclude"),
                ("Completion Rule", "completionRule"),
            ):
                rendered = _compact_prompt_value(handoff_contract.get(key))
                if rendered:
                    lines.append(f"- {label}: {rendered}")
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
                ("Execution Mode", "executionMode"),
                ("Contract Status", "contractStatus"),
                ("Missing Contract Fields", "missingContractFields"),
                ("Parent Capsule", "parentCapsuleId"),
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
    "Use the Engineering Kernel's Active Workspace Root and detected shell dialect; do not spend a tool call rediscovering the bound workspace.\n"
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
    collaboration_identity_context: str = "",
    route_prompt_addition: str = "",
) -> dict[str, object]:
    parts: list[dict[str, str]] = [
        _agent_prompt_part(
            "subagent.delegated_agent_operating_charter",
            "stable_static",
            DELEGATED_AGENT_OPERATING_CHARTER,
            scope="delegation_charter",
        ),
        _agent_prompt_part(
            "subagent.persona_mission_contracts",
            "stable_static",
            f"<system_persona>\nYou are a specialized agent named {agent_name}.\n{agent_system_prompt}\n</system_persona>\n\n",
            scope="persona",
        ),
        _agent_prompt_part(
            "subagent.collaboration_identity",
            "dynamic",
            collaboration_identity_context,
            scope="collaboration_identity",
        ),
        *_split_agent_env_context_parts(env_context),
        _agent_prompt_part("subagent.active_todos", "dynamic", active_plan_context, scope="todos"),
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
    collaboration_identity_context: str = "",
    route_prompt_addition: str = "",
) -> str:
    return str(
        _build_agent_system_bundle(
            agent_name=agent_name,
            agent_system_prompt=agent_system_prompt,
            env_context=env_context,
            active_plan_context=active_plan_context,
            delegated_plan_context=delegated_plan_context,
            collaboration_identity_context=collaboration_identity_context,
            route_prompt_addition=route_prompt_addition,
        )["content"]
    )


def _resolve_inherited_route_context(state: dict, task_messages: list, *, agent_id: str) -> dict[str, list[str] | str]:
    delegated = latest_delegation_context(list(state.get("delegation_contexts") or []), agent_id=agent_id)
    parallel_branch = (
        dict(state.get("parallel_branch") or {})
        if isinstance(state.get("parallel_branch"), dict)
        else {}
    )
    branch_agent_id = str(parallel_branch.get("agentId") or "").strip()
    branch_task_brief = (
        dict(parallel_branch.get("taskBrief") or {})
        if isinstance(parallel_branch.get("taskBrief"), dict)
        else {}
    )
    if branch_task_brief and (not branch_agent_id or branch_agent_id == str(agent_id or "").strip()):
        # A Send branch is the execution truth for this worker.  Additive
        # graph reducers may retain older contexts for the same persistent
        # Agent id (especially disposable grandchildren), so never let a
        # stale parent context replace the branch-local task/tool contract.
        branch_depth = parallel_branch.get("delegationDepth")
        if branch_depth is not None:
            branch_task_brief["delegationDepth"] = branch_depth
        delegated = {
            **dict(delegated or {}),
            "taskBrief": branch_task_brief,
            "query": task_brief_route_query_text(branch_task_brief)
            or task_brief_query_text(branch_task_brief)
            or str(delegated.get("query") or "").strip(),
        }
        for key in (
            "parentDelegationId",
            "delegationId",
            "delegationDepth",
            "delegationNodeCount",
            "delegationBudget",
        ):
            if key in parallel_branch:
                delegated[key] = parallel_branch.get(key)
        delegated["runtimeAccess"] = list(branch_task_brief.get("runtimeAccess") or [])
        return delegated
    if any(delegated.get(key) for key in ("selectedSkillIds", "selectedSkillNames", "selectedSkillEntries", "skillRootDescriptors", "selectedMcpTools", "selectedBaselineTools", "query")):
        return delegated
    current_route_context = dict(state.get("current_route_context") or {})
    if any(current_route_context.get(key) for key in ("selectedSkillIds", "selectedSkillNames", "selectedSkillEntries", "skillRootDescriptors", "selectedMcpTools", "selectedBaselineTools", "query")):
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
    )


def _merged_route_context(state: dict | None, overlay: dict | None) -> dict:
    return merge_route_context(
        dict((state or {}).get("current_route_context") or {}),
        dict(overlay or {}),
    )


def build_contextual_auto_tool_node(
    *,
    base_tools: list,
    agent_data: dict | None = None,
    all_native_tools: list | None = None,
    static_extra_tools: list | None = None,
    all_mcp_tools: list,
    name: str,
    fallback_goto: str,
):
    async def contextual_tool_node(state, config=None, runtime=None):
        route_context = dict((state or {}).get("current_route_context") or {})
        selected_mcp_tools = _resolve_selected_mcp_tools(
            all_mcp_tools,
            list(route_context.get("selectedMcpTools") or []),
        )
        task_brief = route_context.get("taskBrief") if isinstance(route_context.get("taskBrief"), dict) else {}
        runtime_access = _resolve_delegated_runtime_access(
            agent_data,
            (task_brief or {}).get("runtimeAccess") or route_context.get("runtimeAccess"),
            delegation_depth=(
                route_context.get("delegationDepth")
                or route_context.get("delegation_depth")
                or (task_brief or {}).get("delegationDepth")
                or (task_brief or {}).get("delegation_depth")
            ),
        )
        actor_base_tools = filter_visible_tools_for_actor(
            list(all_native_tools or base_tools or []),
            actor="subagent",
            route_context=route_context,
            runtime_access=runtime_access,
        )
        tools = _apply_task_tool_policy(
            _dedupe_tools(actor_base_tools + list(static_extra_tools or []) + selected_mcp_tools),
            task_brief,
        )
        routed = create_routed_tool_node(tools, name=name, fallback_goto=fallback_goto)
        return await routed(state, config=config, runtime=runtime)

    return contextual_tool_node


def build_agent_node(
    *,
    agent_id: str,
    agent_data: dict | None,
    agent_name: str,
    agent_system_prompt: str,
    agent_tool_selectors: list[str],
    agent_tool_mode: str,
    all_mcp_tools: list,
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
        create_subagent_chat_model(
            agent_model_id,
            role=f"agent:{agent_id}",
            streaming=True,
            timeout=180,
        )
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
            command_environment = detect_command_environment()
            current_time = utc_now_iso()
            host_alerts_line = render_host_alerts_line()
            host_alerts_context = f"{host_alerts_line}\n" if host_alerts_line else ""
            engineering_kernel_context, _engineering_kernel_diagnostics = build_engineering_kernel_context(
                state=state,
                session_id=state.get("session_id") or state.get("sessionId"),
                actor="subagent",
            )
            env_context = (
                f"<environment>\n"
                f"OS: {os_name}\n"
                f"Command Shell: {command_environment['commandLanguage']} (shell_dialect={command_environment['shellDialect']})\n"
                "Default Language: zh-CN (简体中文). If the user's current message clearly uses another language, reply in that language.\n"
                f"Current Time: {current_time}\n"
                f"{render_host_load_line()}\n"
                f"{host_alerts_context}"
                f"Active Workspace Root: {workspace_path}\n"
                f"Main V8 Workspace Store: {main_workspace_path}\n"
                "The Active Workspace Root is the execution boundary for delegated project work; keep command cwd and file writes inside it unless the supervisor explicitly grants another root.\n"
                f"When generating visual artifacts, media, or formal reports meant to be viewed in the Web UI, you MUST save them under the Active Workspace Root above.\n"
                "Do NOT expose raw local filesystem paths, raw /api/workspace/files links, or raw <img>/<video>/<audio> HTML in the final reply. "
                "Reference generated media naturally in prose and rely on the runtime artifact/resource pipeline for rendering.\n"
                f"{engineering_kernel_context}"
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
            delegated_context = (
                delegated_task_brief.get("context")
                if isinstance(delegated_task_brief, dict) and isinstance(delegated_task_brief.get("context"), dict)
                else {}
            )
            ephemeral_info = delegated_context.get("ephemeralMirror") if isinstance(delegated_context.get("ephemeralMirror"), dict) else {}
            ephemeral_mirror = bool(ephemeral_info or (delegated_task_brief or {}).get("ephemeralMirror"))
            try:
                delegation_depth = int(
                    inherited_route_context.get("delegationDepth")
                    or (delegated_task_brief or {}).get("delegationDepth")
                    or 0
                )
            except (TypeError, ValueError):
                delegation_depth = 0
            atomic_delegated_worker = bool(ephemeral_mirror or delegation_depth >= 2)
            effective_agent_name = str(
                ephemeral_info.get("name")
                or (state.get("parallel_branch") or {}).get("agentName")
                or agent_name
            ).strip() or agent_name
            effective_agent_system_prompt = agent_system_prompt
            if ephemeral_mirror:
                parent_name = str(ephemeral_info.get("parentAgentName") or agent_name).strip() or agent_name
                effective_agent_system_prompt = (
                    f"You are a disposable execution mirror of the registered subagent {parent_name}.\n"
                    "Execute only the assigned task brief and acceptance contract. Keep context narrow, do not adopt another persistent role, do not create or modify Agent registry assets, and return a compact typed handoff to your parent. Your identity ends when this delegated shard is delivered."
                )
                active_plan_context = ""
            delegated_runtime_access = _resolve_delegated_runtime_access(
                agent_data,
                (delegated_task_brief or {}).get("runtimeAccess"),
                delegation_depth=delegation_depth,
            )
            if delegated_runtime_access:
                delegated_task_brief = {
                    **dict(delegated_task_brief or {}),
                    "runtimeAccess": delegated_runtime_access,
                }
            delegated_plan_context = _format_delegated_task_contract(delegated_task_brief)
            inherited_query = str(inherited_route_context.get("query") or delegated_query).strip() or delegated_query
            full_task_brief_query = task_brief_query_text(delegated_task_brief)
            extensions_route_query = task_brief_route_query_text(delegated_task_brief)
            delegated_query = full_task_brief_query or inherited_query
            extensions_route_query = extensions_route_query or delegated_query
            task_messages = _bounded_delegated_task_messages(messages, delegated_task_brief)
            actor_route_context = {
                **dict(inherited_route_context or {}),
                "taskBrief": delegated_task_brief or {},
            }
            delegated_plugin_references = list(
                (delegated_task_brief or {}).get("pluginReferences")
                or (delegated_task_brief or {}).get("plugin_references")
                or []
            )
            contextual_base_tools = _dedupe_tools(
                filter_visible_tools_for_actor(
                    _select_contextual_subagent_native_tools(filtered_native_tools, delegated_runtime_access)
                    + ([] if atomic_delegated_worker else [fetch_skill_instructions_tool]),
                    actor="subagent",
                    route_context=actor_route_context,
                    runtime_access=delegated_runtime_access,
                )
            )
            explicit_base_tools = _dedupe_tools(
                filter_visible_tools_for_actor(
                    list(filtered_native_tools) + ([] if atomic_delegated_worker else [fetch_skill_instructions_tool]),
                    actor="subagent",
                    route_context=actor_route_context,
                    runtime_access=delegated_runtime_access,
                )
            )
            selected_mcp_tools = _resolve_selected_mcp_tools(all_mcp_tools, agent_tool_selectors)
            explicit_skill_ids, explicit_skill_names = _resolve_selected_skills(agent_tool_selectors, state=state)

            if agent_tool_mode == "explicit":
                base_tools = explicit_base_tools
                available_tools = _dedupe_tools(
                    base_tools + ([] if atomic_delegated_worker else selected_mcp_tools)
                )
                inherited_skill_ids: list[str] = [] if atomic_delegated_worker else explicit_skill_ids
                inherited_skill_names: list[str] = [] if atomic_delegated_worker else explicit_skill_names
            else:
                base_tools = contextual_base_tools
                inherited_mcp_tools = _resolve_selected_mcp_tools(
                    all_mcp_tools,
                    list(inherited_route_context.get("selectedMcpTools") or []),
                )
                inherited_skill_ids = (
                    []
                    if atomic_delegated_worker
                    else list(inherited_route_context.get("selectedSkillIds") or [])
                )
                inherited_skill_names = (
                    []
                    if atomic_delegated_worker
                    else list(inherited_route_context.get("selectedSkillNames") or [])
                )
                if atomic_delegated_worker:
                    available_tools = _dedupe_tools(base_tools)
                elif inherited_mcp_tools or inherited_skill_ids or inherited_skill_names:
                    available_tools = _dedupe_tools(base_tools + inherited_mcp_tools)
                else:
                    available_tools = _dedupe_tools(base_tools + list(all_mcp_tools))

            route_context_token = extensions_runtime_service.bind_execution_context(
                session_id=state.get("session_id"),
                conversation_id=state.get("session_id"),
                run_id=state.get("run_id"),
                agent_id=agent_id,
                workspace_id=state.get("workspace_id"),
                workspace_path=state.get("workspace_path"),
                project_id=state.get("project_id"),
                runtime_kind="subagent",
                delegation_id=inherited_route_context.get("delegationId"),
                delegation_depth=inherited_route_context.get("delegationDepth"),
                plugin_references=delegated_plugin_references,
            )
            try:
                if atomic_delegated_worker:
                    combined_tools = _apply_task_tool_policy(
                        available_tools,
                        delegated_task_brief,
                    )
                    route_bundle = _build_atomic_worker_extension_route(combined_tools)
                else:
                    route_bundle = extensions_runtime_service.build_contextual_route(
                        user_query=extensions_route_query,
                        available_tools=available_tools,
                        loaded_agents=None,
                        inherited_skill_ids=inherited_skill_ids,
                        inherited_skill_names=inherited_skill_names,
                        mcp_limit=6,
                    )
                    combined_tools = _apply_task_tool_policy(
                        route_bundle.filtered_tools,
                        delegated_task_brief,
                    )
                    combined_tools = _preserve_direct_worker_extension_candidates(
                        route_bundle,
                        combined_tools,
                        delegated_task_brief,
                        delegation_depth=delegation_depth,
                    )
                    route_bundle = _align_extension_route_to_task_tools(
                        route_bundle,
                        combined_tools,
                    )
                extensions_runtime_service.emit_route_selected(
                    user_query=extensions_route_query,
                    route_bundle=route_bundle,
                )
            finally:
                extensions_runtime_service.reset_execution_context(route_context_token)

            collaboration_identity_context = _format_collaboration_identity_contract(
                actor_name=effective_agent_name,
                task_brief=delegated_task_brief,
                delegation_depth=delegation_depth,
            )
            system_bundle = _build_agent_system_bundle(
                agent_name=effective_agent_name,
                agent_system_prompt=effective_agent_system_prompt,
                env_context=env_context,
                active_plan_context=active_plan_context,
                delegated_plan_context=delegated_plan_context,
                collaboration_identity_context=collaboration_identity_context,
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
                    agent_name=effective_agent_name,
                    query=extensions_route_query,
                    mode=str(inherited_route_context.get("mode") or "serial"),
                    source_runtime_kind=inherited_route_context.get("sourceRuntimeKind") or "chat",
                    selected_skill_ids=route_bundle.selected_skill_ids,
                    selected_skill_names=route_bundle.selected_skill_names,
                    selected_skill_entries=route_bundle.candidate_summary.get("skillEntries") or [],
                    skill_root_descriptors=route_bundle.skill_root_descriptors or [],
                    selected_mcp_tools=route_bundle.exposed_mcp_tool_names,
                    selected_baseline_tools=select_baseline_system_tool_names(combined_tools),
                    prompt_addition=route_bundle.prompt_addition,
                    invocation_id=inherited_route_context.get("invocationId"),
                    task_brief=delegated_task_brief,
                ),
                "toolMode": agent_tool_mode,
                "runtimeAccess": delegated_runtime_access,
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
                hooks_manager.execute_hook("on_agent_start", agent_name=effective_agent_name, agent_id=agent_id)
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
                runtime_kind="subagent",
                delegation_id=inherited_route_context.get("delegationId"),
                delegation_depth=inherited_route_context.get("delegationDepth"),
                plugin_references=delegated_plugin_references,
            )
            try:
                with bind_runtime_context(
                    session_id=state.get("session_id"),
                    run_id=state.get("run_id"),
                    project_id=state.get("project_id"),
                    workspace_id=state.get("workspace_id"),
                    workspace_path=state.get("workspace_path"),
                    runtime_kind="subagent",
                    trigger_source="delegation_broker",
                    agent_id=agent_id,
                    subagent_id=agent_id,
                    delegation_id=inherited_route_context.get("delegationId"),
                    delegation_depth=inherited_route_context.get("delegationDepth"),
                    root_episode_id=(
                        inherited_route_context.get("rootEpisodeId")
                        or inherited_route_context.get("root_episode_id")
                    ),
                    safety_approval_mode=(
                        state.get("safety_approval_mode")
                        or inherited_route_context.get("safety_approval_mode")
                        or inherited_route_context.get("safetyApprovalMode")
                    ),
                ):
                    response = robust_invoke(
                        agent_specific_llm,
                        run_messages,
                        combined_tools,
                        role=f"agent:{agent_id}",
                        preferred_model_id=agent_model_id or supervisor_model_id,
                        invocation_config=build_runtime_callback_config(),
                        build_model=lambda candidate_model_id: create_subagent_chat_model(
                            candidate_model_id,
                            role=f"agent:{agent_id}",
                            streaming=True,
                            timeout=180,
                        ),
                    )
                    extensions_runtime_service.emit_response_tool_calls(response)
                    extensions_runtime_service.emit_execution_completed(response=response)
            finally:
                extensions_runtime_service.reset_execution_context(route_context_token)

            try:
                hooks_manager.execute_hook(
                    "on_agent_end",
                    agent_name=effective_agent_name,
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
            response = _mark_delegated_message_owner(
                response,
                agent_id=agent_id,
                delegation_id=str(inherited_route_context.get("delegationId") or ""),
            )

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

            final_content = _delegated_visible_result_text(response)
            sub_tools_used = _delegated_tool_names(state["messages"], agent_id=agent_id)

            refined_parts = [f"[{agent_name} 执行完毕]"]
            if sub_tools_used:
                refined_parts.append(f"使用工具: {', '.join(sub_tools_used)}")
            refined_parts.append(f"结果: {final_content}")
            refined_parts.append(
                "\n[Supervisor Acceptance Required]: Inspect this exact result and explicitly accept, retry, or ignore it."
            )
            refined_msg = HumanMessage(
                content="\n".join(refined_parts),
                id=str(uuid.uuid4()),
                additional_kwargs={
                    "v8_governance_type": "delegation_result",
                    "v8_owner_runtime_kind": "subagent",
                    "v8_owner_agent_kind": "subagent",
                    "v8_owner_agent_id": agent_id,
                    "v8_owner_subagent_id": agent_id,
                    "v8_owner_delegation_id": str(inherited_route_context.get("delegationId") or ""),
                    "v8_subagent_tools_used": sub_tools_used,
                    "v8_subagent_result_text": final_content,
                },
            )
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
        create_subagent_chat_model(
            agent_model_id,
            role=f"reviewer:{agent_id}",
            streaming=True,
            timeout=180,
        )
        if agent_model_id
        else default_agent_llm
    )

    def reviewer_node_func(state):
        try:
            messages = state["messages"]
            worker_result_text = _delegated_result_text(messages, agent_id=agent_id)
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
            with bind_runtime_context(
                runtime_kind="subagent",
                trigger_source="delegation_reviewer",
                agent_id=agent_id,
                subagent_id=agent_id,
                delegation_id=(state.get("current_route_context") or {}).get("delegationId"),
                root_episode_id=(
                    (state.get("current_route_context") or {}).get("rootEpisodeId")
                    or (state.get("current_route_context") or {}).get("root_episode_id")
                ),
            ):
                response = robust_invoke(
                    agent_specific_llm,
                    run_messages,
                    None,
                    role=f"reviewer:{agent_id}",
                    preferred_model_id=agent_model_id or supervisor_model_id,
                    invocation_config=build_runtime_callback_config(),
                    build_model=lambda candidate_model_id: create_subagent_chat_model(
                        candidate_model_id,
                        role=f"reviewer:{agent_id}",
                        streaming=True,
                        timeout=180,
                    ),
                )
            response = _mark_delegated_message_owner(
                response,
                agent_id=agent_id,
                delegation_id=str((state.get("current_route_context") or {}).get("delegationId") or ""),
            )
            hooks_manager.execute_hook("on_reviewer_end", agent_name=agent_name, agent_id=agent_id)

            content = str(response.content).strip()
            if "APPROVE" in content or "approve" in content.lower():
                cap_msg = HumanMessage(
                    content=(
                        f"[{agent_name} 执行完毕且通过本地审核]\n"
                        "结果已回流，仍需 Supervisor 基于精确结果明确 accept、retry 或 ignore。"
                    ),
                    id=str(uuid.uuid4()),
                    additional_kwargs={
                        "v8_governance_type": "delegation_result",
                        "v8_owner_runtime_kind": "subagent",
                        "v8_owner_agent_kind": "subagent",
                        "v8_owner_agent_id": agent_id,
                        "v8_owner_subagent_id": agent_id,
                        "v8_owner_delegation_id": str((state.get("current_route_context") or {}).get("delegationId") or ""),
                        "v8_subagent_result_text": worker_result_text,
                    },
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
                _select_contextual_subagent_native_tools(filtered_native_tools, []) + [fetch_skill_instructions],
                actor="subagent",
                runtime_access=[],
            )
        )
        if agent_tool_mode == "contextual_auto":
            # Contextual agents receive ordinary MCP tools only after the
            # delegated task route selects them. Keep the static tool node narrow so
            # the runtime surface does not silently retain the full tree. Explicitly
            # granted plugin tools are projected separately by Plugin Manager.
            tool_node_tools = contextual_base_tools
            tool_node_func = build_contextual_auto_tool_node(
                base_tools=contextual_base_tools,
                agent_data=agent_data,
                all_native_tools=list(filtered_native_tools) + [fetch_skill_instructions],
                static_extra_tools=[],
                all_mcp_tools=all_mcp_tools,
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
            )
            tool_node_func = build_contextual_auto_tool_node(
                base_tools=tool_node_tools,
                agent_data=agent_data,
                all_native_tools=list(filtered_native_tools) + [fetch_skill_instructions],
                static_extra_tools=_resolve_selected_mcp_tools(all_mcp_tools, tool_selectors),
                all_mcp_tools=_resolve_selected_mcp_tools(all_mcp_tools, tool_selectors),
                name=f"{agent_id}_tools",
                fallback_goto=agent_id,
            ) if tool_node_tools else None

        agent_nodes_map[agent_id] = {
            "node_func": build_agent_node(
                agent_id=agent_id,
                agent_data=agent_data,
                agent_name=agent_name,
                agent_system_prompt=agent_sys,
                agent_tool_selectors=tool_selectors,
                agent_tool_mode=agent_tool_mode,
                all_mcp_tools=all_mcp_tools,
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
