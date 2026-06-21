import time
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .supervisor_context import (
    apply_passive_rag_injection,
    build_supervisor_system_content,
    resolve_supervisor_request_context,
)
from .no_progress_breaker import apply_no_progress_breaker
from .supervisor_execution import debug_supervisor_messages, prepare_supervisor_messages
from core.context.delegation import build_delegation_context
from core.memory_observability import log_memory_observation
from core.runtime.extensions_runtime import extensions_runtime_service
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.runtime.reflex_gate import (
    render_gate_prompt_addition,
    render_reflex_prompt_addition,
    runtime_evidence_feedback_service,
    runtime_preflight_gate,
    runtime_reflex_service,
)
from core.system_tools.baseline import select_baseline_system_tool_names


def _last_memory_session_context_diagnostics() -> dict:
    try:
        from core.memory.store import memory_store

        diagnostics = getattr(memory_store, "_last_session_context_diagnostics", {}) or {}
        return dict(diagnostics) if isinstance(diagnostics, dict) else {}
    except Exception:
        return {}


def _last_human_memory_rag_diagnostics(messages) -> dict:
    for message in reversed(list(messages or [])):
        if not hasattr(message, "additional_kwargs"):
            continue
        diagnostics = dict(getattr(message, "additional_kwargs", {}) or {}).get("memory_rag_diagnostics")
        if isinstance(diagnostics, dict):
            return dict(diagnostics)
    return {}


def _estimate_memory_context_chars(diagnostics: dict) -> int:
    total = 0
    for key in ("graphSummarySeedEntities", "consistencyConflicts"):
        value = diagnostics.get(key)
        if isinstance(value, list):
            total += sum(len(str(item)) for item in value[:20])
    return total


def _has_tool(tools, tool_name: str) -> bool:
    expected = str(tool_name or "").strip()
    return any(_tool_ref_name(tool) == expected for tool in list(tools or []))


def _should_force_memory_broker_first(
    *,
    user_query: str,
    passive_rag_diagnostics: dict,
    selected_tools,
    state=None,
) -> bool:
    if not _has_tool(selected_tools, "memory_broker"):
        return False
    if _spec_mode_active(state) and _spec_runtime_execution_allowed(state):
        return False
    if passive_rag_diagnostics.get("has_recall_cue") is True:
        return True
    normalized = str(user_query or "").lower()
    recall_terms = (
        "上一轮",
        "上一次",
        "上次",
        "之前",
        "前面",
        "刚才",
        "历史",
        "记忆",
        "记得",
        "继续上下文",
        "队列消息",
        "同一个 session",
        "same session",
        "previous context",
        "prior context",
    )
    return any(term.lower() in normalized for term in recall_terms)


def _memory_broker_first_guidance(user_query: str) -> SystemMessage:
    query_preview = str(user_query or "").strip().replace("\n", " ")[:220]
    return SystemMessage(
        content=(
            "[Memory Recall Gate]\n"
            "The latest user request depends on previous context, history, memory, queue state, or same-session continuity. "
            "Your first tool call MUST be `memory_broker`, normally `memory_broker(mode=\"recall\", query=\"...\")`. "
            "Do not call `workspace_broker`, `grep_search`, or `read_native_file` before memory_broker for this turn. "
            "If memory_broker returns no matching facts, say that explicitly, then use workspace/session tools only if still needed.\n"
            f"User query preview: {query_preview}"
        )
    )


def _fast_first_turn_guidance() -> SystemMessage:
    return SystemMessage(
        content=(
            "[Fast First Supervisor Turn]\n"
            "Planner is deferred for this first visible turn. Start real work quickly: either make one concise tool call "
            "(for example write_todos/runtime_broker/memory_broker when appropriate) or reply in 1-2 short sentences. "
            "Do not write a long planning narrative in this first turn; continue detailed orchestration in later turns."
        )
    )


def _fast_first_turn_caller_kwargs(caller_kwargs: dict) -> dict:
    fast_kwargs = dict(caller_kwargs or {})
    try:
        configured_max_tokens = int(fast_kwargs.get("max_tokens") or 0)
    except Exception:
        configured_max_tokens = 0
    if not configured_max_tokens or configured_max_tokens > _FAST_FIRST_TURN_MAX_TOKENS:
        fast_kwargs["max_tokens"] = _FAST_FIRST_TURN_MAX_TOKENS
    return fast_kwargs


def _is_network_supervisor_compat_transport(state) -> bool:
    route_context = dict((state or {}).get("current_route_context") or {}) if isinstance(state, dict) else {}
    transport = str(
        (state or {}).get("transport")
        or route_context.get("transport")
        or route_context.get("triggerSource")
        or ""
    ).strip()
    return transport in {"network_supervisor_openai", "network_supervisor_anthropic"}


def _compat_ingress_diagnostics_from_state(state) -> dict:
    route_context = dict((state or {}).get("current_route_context") or {}) if isinstance(state, dict) else {}
    diagnostics = route_context.get("compatIngressDiagnostics") or route_context.get("compat_ingress_diagnostics")
    return dict(diagnostics) if isinstance(diagnostics, dict) else {}


def _compat_external_tools_primary(state) -> bool:
    diagnostics = _compat_ingress_diagnostics_from_state(state)
    if "externalToolsPrimary" in diagnostics:
        return bool(diagnostics.get("externalToolsPrimary"))
    route_context = dict((state or {}).get("current_route_context") or {}) if isinstance(state, dict) else {}
    if "externalToolsPrimary" in route_context:
        return bool(route_context.get("externalToolsPrimary"))
    return False


def _compat_suppress_extensions_prefilter(state) -> bool:
    diagnostics = _compat_ingress_diagnostics_from_state(state)
    if "suppressExtensionsPrefilter" in diagnostics:
        return bool(diagnostics.get("suppressExtensionsPrefilter"))
    return _is_network_supervisor_compat_transport(state) and _compat_external_tools_primary(state)


def _compat_suppress_passive_rag(state) -> tuple[bool, str]:
    diagnostics = _compat_ingress_diagnostics_from_state(state)
    if "suppressPassiveRag" in diagnostics:
        return bool(diagnostics.get("suppressPassiveRag")), str(
            diagnostics.get("ragSkipReason") or diagnostics.get("skipReason") or "compat_classifier_suppressed_passive_rag"
        )
    return False, ""


_COMPAT_ALLOWED_INTERNAL_TOOL_NAMES = {
    "tool_observation_detail",
}


_SUPERVISOR_TODO_TOOL_NAMES = {"write_todos", "update_todo"}
_SPEC_BROKER_TOOL_NAMES = {"spec_broker"}
_FAST_FIRST_TURN_MAX_TOKENS = 900
_SPEC_MODE_INITIAL_ALLOWED_TOOL_NAMES = {
    "ask_user",
    "fetch_skill_instructions",
    "memory_broker",
    "research_broker",
    "spec_broker",
    "tool_observation_detail",
    "web_broker",
}
_SPEC_MODE_ALLOWED_TOOL_NAMES = {
    "ask_user",
    "fetch_skill_instructions",
    "memory_broker",
    "research_broker",
    "spec_broker",
    "tool_observation_detail",
    "web_broker",
}
_SPEC_MODE_EXECUTION_TOOL_NAMES = {
    "ask_user",
    "runtime_broker",
    "spec_broker",
    "tool_observation_detail",
}


def _tool_ref_name(tool_ref) -> str:
    return str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip()


def _filter_network_supervisor_compat_tools(tools):
    filtered = []
    for tool_ref in list(tools or []):
        name = _tool_ref_name(tool_ref)
        if not name:
            continue
        if name.startswith("network_") or name in _COMPAT_ALLOWED_INTERNAL_TOOL_NAMES:
            filtered.append(tool_ref)
    return filtered


def _filter_tool_names(tools, excluded_names: set[str]):
    excluded = {str(name or "").strip() for name in excluded_names if str(name or "").strip()}
    return [tool_ref for tool_ref in list(tools or []) if _tool_ref_name(tool_ref) not in excluded]


def _spec_mode_active(state) -> bool:
    if not isinstance(state, dict):
        return False
    route_context = dict(state.get("current_route_context") or {})
    return bool(
        state.get("specMode")
        or state.get("spec_mode")
        or route_context.get("specMode")
        or route_context.get("spec_mode")
    )


def _spec_id_from_state(state) -> str:
    if not isinstance(state, dict):
        return ""
    route_context = dict(state.get("current_route_context") or {})
    for container in (state, route_context):
        continuation = container.get("specContinuation") if isinstance(container, dict) else None
        if isinstance(continuation, dict):
            value = continuation.get("specId") or continuation.get("spec_id")
            if str(value or "").strip():
                return str(value).strip()
    for key in ("specId", "spec_id"):
        value = state.get(key) or route_context.get(key)
        if str(value or "").strip():
            return str(value).strip()
    for key in ("spec_brief", "specBrief"):
        candidate = state.get(key)
        if isinstance(candidate, dict) and str(candidate.get("specId") or candidate.get("spec_id") or "").strip():
            return str(candidate.get("specId") or candidate.get("spec_id")).strip()
        candidate = route_context.get(key)
        if isinstance(candidate, dict) and str(candidate.get("specId") or candidate.get("spec_id") or "").strip():
            return str(candidate.get("specId") or candidate.get("spec_id")).strip()
    return ""


def _spec_runtime_execution_allowed(state) -> bool:
    if not isinstance(state, dict):
        return False
    route_context = dict(state.get("current_route_context") or {})
    for container in (state, route_context):
        continuation = container.get("specContinuation") if isinstance(container, dict) else None
        if isinstance(continuation, dict) and bool(continuation.get("runtimeExecutionAllowed")):
            return True
    if bool(
        state.get("runtimeAllowed")
        or state.get("runtimeExecutionAllowed")
        or route_context.get("runtimeAllowed")
        or route_context.get("runtimeExecutionAllowed")
    ):
        return True
    candidates = (
        state.get("spec_brief"),
        state.get("specBrief"),
        route_context.get("specBrief"),
        route_context.get("spec_brief"),
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        pipeline = candidate.get("pipelineControl") if isinstance(candidate.get("pipelineControl"), dict) else {}
        if bool(pipeline.get("runtimeExecutionAllowed")):
            return True
    return False


def _spec_next_stage_from_state(state) -> str:
    if not isinstance(state, dict):
        return ""
    route_context = dict(state.get("current_route_context") or {})
    for container in (state, route_context):
        continuation = container.get("specContinuation") if isinstance(container, dict) else None
        if isinstance(continuation, dict):
            value = str(continuation.get("nextStage") or continuation.get("next_stage") or "").strip().lower()
            if value:
                return value
    candidates = (
        state.get("spec_brief"),
        state.get("specBrief"),
        route_context.get("specBrief"),
        route_context.get("spec_brief"),
    )
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        pipeline = candidate.get("pipelineControl") if isinstance(candidate.get("pipelineControl"), dict) else {}
        value = str(pipeline.get("nextStage") or "").strip().lower()
        if value:
            return value
    return ""


def _spec_tasks_authoring_guidance() -> str:
    return (
        "Tasks stage rule: requirements/design may be loose, but tasks.md must be strict enough for runtime/subagent dispatch.\n"
        "Use this minimum format inside spec_broker(content=...):\n"
        "## Task Pipeline\n"
        "| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        "| TASK-001 | Research | Gather evidence for the topic. | - | REQ-001, DES-001 | references/research/*.md + evidence pack | Sources and limits are recorded. |\n"
        "| TASK-002 | Engineering | Create/update the requested artifact. | TASK-001 | REQ-001, DES-002 | target files/artifact paths | Files exist and pass checks. |\n\n"
        "## Task Details\n"
        "### TASK-001: <task title>\n"
        "- runtimeLane: Research\n"
        "- dependsOn: []\n"
        "- specRefs: REQ-001, DES-001\n"
        "- inputRefs: approved requirements/design sections\n"
        "- expectedOutput: <paths or handoff names>\n"
        "- acceptance: <how to verify>\n"
        "- proofRequired: <proof/handoff/artifact refs>\n"
        "If the approved requirements/design do not have formal REQ/DES IDs, cite their explicit section titles or numbered clauses in specRefs."
    )


def _filter_spec_tools_for_mode(tools, state):
    if _spec_mode_active(state):
        allowed = _SPEC_MODE_ALLOWED_TOOL_NAMES
        if not _spec_id_from_state(state):
            allowed = _SPEC_MODE_INITIAL_ALLOWED_TOOL_NAMES
        elif _spec_runtime_execution_allowed(state):
            allowed = _SPEC_MODE_EXECUTION_TOOL_NAMES
        return [
            tool_ref
            for tool_ref in list(tools or [])
            if _tool_ref_name(tool_ref) in allowed
        ]
    return _filter_tool_names(tools, _SPEC_BROKER_TOOL_NAMES)


def _selected_skill_names_from_state(state, messages=None) -> list[str]:
    names: list[str] = []

    def add(value) -> None:
        text = str(value or "").strip()
        if text and text not in names:
            names.append(text)

    if isinstance(state, dict):
        route_context = dict(state.get("current_route_context") or {})
        for value in list(route_context.get("selectedSkillNames") or []):
            add(value)
        for key in ("skill_references", "skillReferences"):
            for item in list(state.get(key) or route_context.get(key) or []):
                if isinstance(item, dict):
                    add(item.get("name") or item.get("id"))
        for mention in list(state.get("context_mentions") or state.get("contextMentions") or []):
            if not isinstance(mention, dict):
                continue
            if str(mention.get("kind") or "").strip().lower() == "skill":
                add(mention.get("name") or mention.get("id") or mention.get("label"))

    for message in list(messages or []):
        content = str(getattr(message, "content", "") or "")
        if "[SKILL REFERENCES]" not in content:
            continue
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("- name:"):
                add(stripped.split(":", 1)[1])
    return names


def _looks_like_skill_creation_request(user_query: str) -> bool:
    text = str(user_query or "").strip().lower()
    if not text:
        return False
    return (
        ("skill" in text and any(marker in text for marker in ("生成", "创建", "造", "做", "write", "create", "generate")))
        or "造skill" in text
        or "造 skill" in text
        or "女娲" in text
        or "huashu-nuwa" in text
    )


def _spec_mode_stage_guidance(*, state, user_query: str, selected_tools, messages=None) -> SystemMessage | None:
    if not _spec_mode_active(state):
        return None
    if _spec_runtime_execution_allowed(state):
        if not _has_tool(selected_tools, "runtime_broker"):
            return None
        spec_id = _spec_id_from_state(state) or "current approved spec"
        return SystemMessage(
            content=(
                "[Spec Runtime Execution Gate]\n"
                f"Spec Mode is active for specId={spec_id}, and the user has approved requirements, design, and tasks. "
                "The next durable action MUST route the approved Spec into execution with `runtime_broker(mode='route', need=...)` unless you must ask the user for a missing permission/scope. "
                "The `need` payload should cite `specId`, task/detail refs, required runtime lanes, expected artifacts, and proof expectations from the approved tasks. "
                "Use Engineering/Research/Delegation/Creative runtimes as appropriate; if the Spec or selected skill calls for broad research or subagent swarms, express that as Research Runtime and/or top-level Delegation episodes, not hidden Supervisor-only work. "
                "`runtime_execution` is not a Spec document stage; do not call `spec_broker(stage='runtime_execution')` or try to write another Spec stage. "
                "Do not call memory_broker, fetch_skill_instructions, research_broker, web_broker, delegation_broker, or write_native_file directly in this stage; route those needs through runtime_broker. "
                "If a runtime episode fails or returns degraded handoff, repair or reroute the same approved specId; do not start a new bugfix/requirements Spec unless the user explicitly asks for a new Spec. "
                "Supervisor todo tools are hidden in Spec Mode; use approved Spec tasks as the execution contract and runtime episode ledgers/proofs as execution truth. "
                "Use `spec_broker(mode='brief'|'read_section')` only when you need exact approved task/spec refs for the runtime route."
            )
        )
    if not _has_tool(selected_tools, "spec_broker"):
        return None
    spec_id = _spec_id_from_state(state)
    if spec_id:
        next_stage = _spec_next_stage_from_state(state)
        tasks_guidance = (
            "\n" + _spec_tasks_authoring_guidance()
            if next_stage == "tasks"
            else ""
        )
        return SystemMessage(
            content=(
                "[Spec Pipeline Gate]\n"
                f"Spec Mode is active for specId={spec_id}. Runtime execution is still blocked by the Spec pipeline. "
                "Use `spec_broker(mode='read'|'read_stage')` to inspect the current stage; "
                "use `spec_broker(mode='write_stage'|'rewrite_stage'|'edit'|'write'|'update', stage='requirements|bugfix|design|tasks', content='<markdown>')` to write or revise it; "
                "do not call `spec_broker(mode='approve')` yourself. Approval is a user/client governance event; wait for the resumed turn carrying the approved nextStage. "
                "Spec documents are not final project deliverables; after approved tasks, use `runtime_broker(mode='route', need=...)` so the approved tasks become runtime episodes with proof/artifact handoff. "
                "If a selected skill asks for parallel subagents, Agent Swarm, or many research shards, translate that into Research Runtime evidence or explicit top-level delegation after the relevant Spec stage is approved; do not assume subagents can spawn grandchildren implicitly. "
                "Do not route Engineering/Research/Delegation runtime work until the Spec brief says runtimeExecutionAllowed=true."
                f"{tasks_guidance}"
            )
        )
    skill_names = _selected_skill_names_from_state(state, messages=messages)
    if _looks_like_skill_creation_request(user_query) and "skill-creator" not in {item.lower() for item in skill_names}:
        skill_names.append("skill-creator")
    skill_instruction = ""
    if skill_names and _has_tool(selected_tools, "fetch_skill_instructions"):
        calls = ", ".join(f"fetch_skill_instructions(skill_name={name!r}, detail_level='full')" for name in skill_names[:3])
        skill_instruction = (
            "Before writing the first Spec stage, read the selected/required skill contracts with real tool calls: "
            f"{calls}. Do not replace these reads with memory, research, or prose claims.\n"
        )
    return SystemMessage(
        content=(
            "[Spec Pipeline Gate]\n"
            "Spec Mode is active but no specId exists yet. The first durable action for this request must be a real "
            "`spec_broker(mode='write_stage', stage='requirements'|'bugfix', content='<approval-quality markdown>')` call. "
            f"{skill_instruction}"
            "You may use `memory_broker`, `research_broker`, or `web_broker` only for bounded discovery needed to write a better Spec stage; "
            "keep discovery concise and cite the evidence in the Spec content. "
            "If a selected skill asks for parallel subagents, Agent Swarm, or many research shards, write that execution requirement into the Spec and use `research_broker` only for bounded pre-Spec discovery; do not call Delegation or assume subagents can spawn grandchildren before approval. "
            "Do not call `runtime_broker`, Engineering, Delegation, or other execution runtimes before the initial Spec stage exists. "
            "If requirements are unclear, ask the user; otherwise create the first stage via spec_broker after any necessary discovery."
        )
    )


def _task_shape_from_state(state) -> dict:
    if not isinstance(state, dict):
        return {}
    task_shape = state.get("task_shape_hint")
    if isinstance(task_shape, dict) and task_shape:
        return dict(task_shape)
    route_context = state.get("current_route_context")
    if isinstance(route_context, dict) and isinstance(route_context.get("taskShapeHint"), dict):
        return dict(route_context.get("taskShapeHint") or {})
    return {}


def _should_hide_todo_tools_for_direct_writing(state, user_query: str) -> bool:
    task_shape = _task_shape_from_state(state)
    writing_route = task_shape.get("writingRoute") if isinstance(task_shape.get("writingRoute"), dict) else {}
    if not writing_route.get("present"):
        return False
    mode = str(writing_route.get("mode") or "").strip()
    if mode not in {"direct_supervisor", "ask_user_clarify"}:
        return False
    if bool(writing_route.get("requiresArtifact")) or bool(writing_route.get("requiresResearch")):
        return False
    if str(writing_route.get("recommendedFamily") or "").strip() or str(writing_route.get("preferredAgentId") or "").strip():
        return False
    query = str(user_query or "")
    explicit_runtime_terms = (
        "runtime",
        "subagent",
        "子代理",
        "工程 runtime",
        "调研 runtime",
        "派发",
        "多 agent",
        "多智能体",
    )
    if any(term.lower() in query.lower() for term in explicit_runtime_terms):
        return False
    return True


def _build_neutral_extensions_route(visible_supervisor_tools):
    return SimpleNamespace(
        filtered_tools=list(visible_supervisor_tools or []),
        prompt_addition="",
        selected_skill_ids=[],
        selected_skill_names=[],
        exposed_mcp_tool_names=[],
        skill_root_descriptors=[],
        candidate_summary={
            "skillEntries": [],
            "pluginHostTools": [],
            "compatIngressFiltering": True,
            "reason": "network_supervisor_compat_disables_v8_extensions_prefilter",
        },
    )


def _explicit_extension_request_in_query(user_query: str) -> bool:
    query = str(user_query or "").strip().lower()
    if not query:
        return False
    markers = (
        "@",
        "skill",
        "skills",
        "mcp",
        "context7",
        "fetch_skill",
        "fetch skill",
        "技能",
        "调用技能",
        "使用技能",
        "预筛",
        "女娲",
        "huashu",
        "nuwa",
    )
    return any(marker in query for marker in markers)


def _should_use_fast_first_turn_route(state, user_query: str) -> bool:
    if not isinstance(state, dict):
        return False
    route_context = dict(state.get("current_route_context") or {})
    planner_deferred = bool(
        state.get("plannerDeferredFirstTurn")
        or state.get("planner_deferred_first_turn")
        or route_context.get("plannerDeferredFirstTurn")
        or route_context.get("planner_deferred_first_turn")
    )
    if not planner_deferred:
        return False
    if _spec_mode_active(state):
        return False
    return not _explicit_extension_request_in_query(user_query)


def _should_use_spec_narrow_route(state) -> bool:
    return _spec_mode_active(state) and not _spec_runtime_execution_allowed(state)


def _attach_route_context_to_response(response, *, user_query: str, route_bundle, selected_tools) -> None:
    prefilter_signature = _extensions_prefilter_signature(route_bundle)
    payload = {
        "query": user_query,
        "selectedSkillIds": list(route_bundle.selected_skill_ids or []),
        "selectedSkillNames": list(route_bundle.selected_skill_names or []),
        "selectedSkillEntries": list(route_bundle.candidate_summary.get("skillEntries") or []),
        "skillRootDescriptors": list(route_bundle.skill_root_descriptors or []),
        "selectedMcpTools": list(route_bundle.exposed_mcp_tool_names or []),
        "selectedPluginHostTools": list(route_bundle.candidate_summary.get("pluginHostTools") or []),
        "extensionsPrefilterSignature": prefilter_signature,
        "extensionsPrefilterQuery": str(user_query or "").strip(),
    }
    delegation_context = build_delegation_context(
        mode="route",
        query=user_query,
        source_runtime_kind="chat",
        selected_skill_ids=route_bundle.selected_skill_ids,
        selected_skill_names=route_bundle.selected_skill_names,
        selected_skill_entries=route_bundle.candidate_summary.get("skillEntries") or [],
        skill_root_descriptors=route_bundle.skill_root_descriptors or [],
        selected_mcp_tools=route_bundle.exposed_mcp_tool_names,
        selected_plugin_host_tools=route_bundle.candidate_summary.get("pluginHostTools") or [],
        selected_baseline_tools=select_baseline_system_tool_names(selected_tools),
        prompt_addition=route_bundle.prompt_addition,
    )
    additional_kwargs = dict(getattr(response, "additional_kwargs", None) or {})
    additional_kwargs["v8_route_context"] = payload
    additional_kwargs["v8_delegation_context"] = delegation_context
    response.additional_kwargs = additional_kwargs


def _extensions_prefilter_signature(route_bundle) -> str:
    summary = dict(getattr(route_bundle, "candidate_summary", None) or {})
    parts = [
        str(summary.get("skillInventoryRevision") or ""),
        str(summary.get("visibleRootSignature") or ""),
        str(summary.get("visibleRootRevisionKey") or ""),
        str(summary.get("mcpInventoryRevision") or ""),
        str(summary.get("lexiconSignature") or ""),
        ",".join(str(item) for item in list(summary.get("changedRoots") or [])),
        ",".join(str(key) for key in sorted(dict(summary.get("mcpChangedServers") or {}).keys())),
    ]
    return "|".join(parts)


def _latest_message_is_true_user_input(messages) -> bool:
    ordered = list(messages or [])
    if not ordered:
        return False
    latest = ordered[-1]
    if not isinstance(latest, HumanMessage):
        return False
    content = str(getattr(latest, "content", "") or "").strip()
    if not content:
        return False
    internal_prefixes = (
        "[Runtime Recoverable Failure]",
        "[Runtime Episode Recoverable Failure]",
        "[System Resume]",
        "[Tool Observation]",
    )
    return not any(content.startswith(prefix) for prefix in internal_prefixes)


def _should_include_extensions_prefilter_prompt(*, state, messages, user_query: str, route_bundle) -> bool:
    if _latest_message_is_true_user_input(messages):
        return True
    route_context = dict((state or {}).get("current_route_context") or {}) if isinstance(state, dict) else {}
    previous_signature = str(route_context.get("extensionsPrefilterSignature") or "").strip()
    current_signature = _extensions_prefilter_signature(route_bundle)
    if not previous_signature:
        return True
    if current_signature and current_signature != previous_signature:
        return True
    previous_query = str(route_context.get("extensionsPrefilterQuery") or route_context.get("query") or "").strip()
    return bool(str(user_query or "").strip() and previous_query and str(user_query or "").strip() != previous_query)


def _suppress_extensions_prefilter_prompt(route_bundle, *, reason: str = "prefilter_static_until_next_user_message"):
    try:
        route_bundle.prompt_addition = ""
        summary = dict(route_bundle.candidate_summary or {})
        summary["promptSuppressedReason"] = reason
        summary["prefilterPseudoStatic"] = True
        route_bundle.candidate_summary = summary
    except Exception:
        pass
    return route_bundle


def _runtime_episode_handoff_ready(state) -> bool:
    if not isinstance(state, dict):
        return False
    dispatch_status = state.get("planner_dispatch_status")
    if not isinstance(dispatch_status, dict):
        return False
    mode = str(dispatch_status.get("mode") or "").strip()
    next_action = str(dispatch_status.get("nextAction") or "").strip()
    dispatch_state = str(dispatch_status.get("state") or "").strip()
    if mode != "runtime_episode" or next_action != "resume_supervisor":
        return False
    if dispatch_state not in {"handoff_ready", "degraded_handoff_ready", "episode_terminal"}:
        return False
    if dispatch_state in {"handoff_ready", "degraded_handoff_ready"}:
        return True
    try:
        return int(dispatch_status.get("handoffCount") or 0) > 0
    except Exception:
        return False


def _runtime_episode_recoverable_failure(state) -> bool:
    if not isinstance(state, dict):
        return False
    dispatch_status = state.get("planner_dispatch_status")
    if not isinstance(dispatch_status, dict):
        return False
    mode = str(dispatch_status.get("mode") or "").strip()
    next_action = str(dispatch_status.get("nextAction") or "").strip()
    dispatch_state = str(dispatch_status.get("state") or "").strip()
    return mode == "runtime_episode" and next_action == "recoverable_failure" and bool(dispatch_state)


def _runtime_handoff_final_message() -> HumanMessage:
    return HumanMessage(
        content=(
            "[Runtime Completion Instruction]\n"
            "Runtime episodes are terminal and their typed handoffs are already available in the conversation. "
            "Now produce one concise user-facing completion/status summary and then stop. "
            "Do not call tools, do not grant/list/route runtimes, do not inspect files, and do not start new research "
            "unless the user sends a new request."
        )
    )


def _runtime_handoff_final_text(state) -> str:
    route_context = dict((state or {}).get("current_route_context") or {})
    dispatch_status = dict((state or {}).get("planner_dispatch_status") or {})
    handoffs = [
        dict(item)
        for item in list(route_context.get("handoffRefs") or [])
        if isinstance(item, dict)
    ]
    state_label = str(dispatch_status.get("state") or "").strip()
    heading = (
        "运行时链路已降级回流，当前可见结果如下："
        if state_label == "degraded_handoff_ready"
        else "运行时链路已经完成并回流，当前可见结果如下："
    )
    lines = [heading]
    for handoff in handoffs[:8]:
        kind = str(handoff.get("kind") or handoff.get("type") or "runtime_handoff").strip()
        status = str(handoff.get("status") or "").strip()
        summary = str(handoff.get("compactSummary") or handoff.get("summary") or "").strip()
        if not summary:
            refs = handoff.get("refs") if isinstance(handoff.get("refs"), list) else []
            summary = f"已生成 {len(refs)} 个引用。" if refs else "已生成 typed handoff。"
        label = kind if not status else f"{kind} / {status}"
        lines.append(f"- {label}: {summary[:900]}")
    if len(handoffs) > 8:
        lines.append(f"- 另有 {len(handoffs) - 8} 个 handoff 已进入执行图/诊断面板。")
    if not handoffs:
        episode_count = int(dispatch_status.get("episodeCount") or 0)
        lines.append(f"- runtime_episode: {episode_count} 个 episode 已进入终态，但没有可展示 handoff 引用。")
    lines.append("我会基于这些 runtime 结果完成本轮收口；不会绕过 runtime 直接执行受管控的文件或命令操作。")
    return "\n".join(lines)


def _runtime_handoff_final_response(state) -> AIMessage:
    return AIMessage(content=_runtime_handoff_final_text(state))


def _runtime_recoverable_failure_final_text(state) -> str:
    dispatch_status = dict((state or {}).get("planner_dispatch_status") or {})
    reason = str(dispatch_status.get("reason") or dispatch_status.get("state") or "runtime_episode_failed").strip()
    failed_episode_count = int(dispatch_status.get("failedEpisodeCount") or dispatch_status.get("episodeCount") or 0)
    failed_handoff_count = int(dispatch_status.get("failedHandoffCount") or 0)
    lines = [
        "这次任务还没有真正完成：必需的 runtime episode 已失败。",
        f"- 失败原因：{reason}",
    ]
    if failed_episode_count:
        lines.append(f"- 失败 episode 数：{failed_episode_count}")
    if failed_handoff_count:
        lines.append(f"- 失败 handoff 数：{failed_handoff_count}")
    lines.append("我不会把失败或未生成的产物当作已交付；需要根据这个失败原因重新缩小任务、修复 worker/验收合同，或由用户确认后重试。")
    return "\n".join(lines)


def _runtime_recoverable_failure_final_response(state) -> AIMessage:
    return AIMessage(content=_runtime_recoverable_failure_final_text(state))


def _runtime_recoverable_failure_message(state) -> HumanMessage:
    dispatch_status = dict((state or {}).get("planner_dispatch_status") or {})
    reason = str(dispatch_status.get("reason") or dispatch_status.get("state") or "runtime_episode_failed").strip()
    return HumanMessage(
        content=(
            "[Runtime Recoverable Failure]\n"
            "A runtime episode that was required for the current user request failed or produced a failed handoff. "
            "You MUST NOT claim the user request is complete, and you MUST NOT summarize failed artifacts as delivered.\n"
            f"Failure reason: {reason}\n"
            "If the request is still actionable, continue through the runtime/subagent route with a concrete repair task "
            "that includes the failed acceptance reason and required artifact contract. If you cannot repair it in this turn, "
            "tell the user the exact blocker and what still needs to be fixed. Do not call direct mutation tools to bypass "
            "runtime gate."
        )
    )


def _has_runtime_recoverable_failure_message(messages, reason: str) -> bool:
    marker = "[Runtime Recoverable Failure]"
    normalized_reason = str(reason or "").strip()
    for message in messages or []:
        content = str(getattr(message, "content", "") or "")
        if marker not in content:
            continue
        if not normalized_reason or normalized_reason in content:
            return True
    return False


def _response_has_tool_calls(response) -> bool:
    if getattr(response, "tool_calls", None):
        return True
    additional_kwargs = dict(getattr(response, "additional_kwargs", None) or {})
    return bool(additional_kwargs.get("tool_calls") or additional_kwargs.get("toolCalls"))


def _coerce_recoverable_failure_response(response, state):
    if not _runtime_episode_recoverable_failure(state) or _response_has_tool_calls(response):
        return response
    content = str(getattr(response, "content", "") or "")
    if any(marker in content for marker in ("未完成", "失败", "阻塞", "需要修复", "recoverable", "failed", "blocked")):
        return response
    dispatch_status = dict((state or {}).get("planner_dispatch_status") or {})
    reason = str(dispatch_status.get("reason") or dispatch_status.get("state") or "runtime_episode_failed").strip()
    response.content = (
        "这次任务还没有真正完成：必需的 runtime episode 已失败，"
        f"失败原因是 `{reason}`。我不会把失败的产物当作已交付；需要继续走 runtime/subagent 修复链路，"
        "或根据失败原因补齐验收要求后重试。"
    )
    return response


def execute_supervisor_turn(
    *,
    state,
    config,
    messages,
    loaded_agents,
    supervisor_tools,
    memory_runtime,
    scope_resolution_service,
    ensure_reasoning_content,
    sanitize_message_chain,
    context_orchestrator,
    robust_invoke,
    supervisor_base_llm,
    sup_model_name,
    caller_kwargs,
    llm_factory,
    sanitize_response_tool_calls,
):
    compat_diagnostics = _compat_ingress_diagnostics_from_state(state)
    context_info = resolve_supervisor_request_context(messages, scope_resolution_service)
    user_query = context_info["user_query"]
    compat_latest_human = str(compat_diagnostics.get("latestHumanUtterance") or "").strip()
    if _is_network_supervisor_compat_transport(state) and compat_latest_human:
        user_query = compat_latest_human
    current_scope = context_info["current_scope"]
    scope_chain = context_info["scope_chain"]
    session_id = context_info["session_id"]
    route_context_token = extensions_runtime_service.bind_execution_context(
        session_id=session_id,
        conversation_id=session_id,
        run_id=state.get("run_id"),
        agent_id="supervisor",
        workspace_id=state.get("workspace_id"),
        workspace_path=state.get("workspace_path"),
        project_id=state.get("project_id"),
        runtime_kind="chat",
    )
    try:
        visible_supervisor_tools = filter_visible_tools_for_actor(
            supervisor_tools,
            actor="supervisor",
            route_context=dict(state.get("current_route_context") or {}),
        )
        visible_supervisor_tools = _filter_spec_tools_for_mode(visible_supervisor_tools, state)
        if _runtime_episode_handoff_ready(state):
            response = _runtime_handoff_final_response(state)
            extensions_runtime_service.emit_execution_completed(response=response)
            return response
        if _runtime_episode_recoverable_failure(state):
            response = _runtime_recoverable_failure_final_response(state)
            extensions_runtime_service.emit_execution_completed(response=response)
            return response
        if _is_network_supervisor_compat_transport(state) and _compat_external_tools_primary(state):
            visible_supervisor_tools = _filter_network_supervisor_compat_tools(visible_supervisor_tools)
        route_started_at = time.perf_counter()
        fast_first_turn_route = _should_use_fast_first_turn_route(state, user_query)
        spec_narrow_route = _should_use_spec_narrow_route(state)
        if spec_narrow_route:
            route_bundle = _build_neutral_extensions_route(visible_supervisor_tools)
            route_bundle.candidate_summary["reason"] = "spec_mode_stage_uses_narrow_tool_surface"
            route_duration_ms = 0.0
        elif _is_network_supervisor_compat_transport(state) and _compat_suppress_extensions_prefilter(state):
            route_bundle = _build_neutral_extensions_route(visible_supervisor_tools)
            route_duration_ms = 0.0
        else:
            route_bundle = extensions_runtime_service.build_supervisor_route(
                user_query=user_query,
                supervisor_tools=visible_supervisor_tools,
                loaded_agents=loaded_agents,
            )
            route_duration_ms = round((time.perf_counter() - route_started_at) * 1000, 2)
        include_extensions_prefilter_prompt = _should_include_extensions_prefilter_prompt(
            state=state,
            messages=messages,
            user_query=user_query,
            route_bundle=route_bundle,
        )
        if not include_extensions_prefilter_prompt:
            route_bundle = _suppress_extensions_prefilter_prompt(route_bundle)
        filtered_supervisor_tools = route_bundle.filtered_tools
        filtered_supervisor_tools = _filter_spec_tools_for_mode(filtered_supervisor_tools, state)
        try:
            route_bundle.filtered_tools = list(filtered_supervisor_tools)
        except Exception:
            pass
        if _should_hide_todo_tools_for_direct_writing(state, user_query):
            filtered_supervisor_tools = _filter_tool_names(filtered_supervisor_tools, _SUPERVISOR_TODO_TOOL_NAMES)
        if not _is_network_supervisor_compat_transport(state) and include_extensions_prefilter_prompt:
            extensions_runtime_service.emit_route_selected(user_query=user_query, route_bundle=route_bundle)

        reflex_decision = runtime_reflex_service.evaluate(
            user_query=user_query,
            scope=current_scope,
            scope_chain=scope_chain,
            session_id=session_id,
            route_bundle=route_bundle,
            state=state,
        )
        gate_decision = runtime_preflight_gate.evaluate(
            user_query=user_query,
            scope=current_scope,
            scope_chain=scope_chain,
            session_id=session_id,
            route_bundle=route_bundle,
            state=state,
        )
        reflex_prompt_addition = render_reflex_prompt_addition(reflex_decision)
        gate_prompt_addition = render_gate_prompt_addition(gate_decision)

        prompt_started_at = time.perf_counter()
        context_bundle = build_supervisor_system_content(
            state=state,
            config=config,
            user_query=user_query,
            current_scope=current_scope,
            scope_chain=scope_chain,
            session_id=session_id,
            messages=messages,
            loaded_agents=loaded_agents,
            supervisor_tools=filtered_supervisor_tools,
            memory_runtime=memory_runtime,
            extension_prompt_addition=route_bundle.prompt_addition,
            reflex_prompt_addition=reflex_prompt_addition,
            gate_prompt_addition=gate_prompt_addition,
        )
        prompt_duration_ms = round((time.perf_counter() - prompt_started_at) * 1000, 2)
        system_content = context_bundle["system_content"]
        memory_diagnostics = _last_memory_session_context_diagnostics()
        log_memory_observation(
            "passive_context",
            "INFO",
            trigger="supervisor_turn",
            sessionId=session_id,
            runId=state.get("run_id") or state.get("runId"),
            callsLlm=False,
            scope=current_scope,
            graphSummaryInjected=bool(memory_diagnostics.get("graphSummaryInjected")),
            graphSummaryRelationCount=int(memory_diagnostics.get("graphSummaryRelationCount") or 0),
            consistencyNoteInjected=bool(memory_diagnostics.get("consistencyNoteInjected")),
            consistencyConflictCount=len(memory_diagnostics.get("consistencyConflicts") or []),
            inputCharEstimate=_estimate_memory_context_chars(memory_diagnostics),
        )
        evidence_feedback_packet = runtime_evidence_feedback_service.record(
            session_id=session_id,
            run_id=state.get("run_id") or state.get("runId"),
            scope=current_scope,
            reflex_decision=reflex_decision,
            gate_decision=gate_decision,
            memory_diagnostics=memory_diagnostics,
            route_bundle=route_bundle,
            state=state,
        )

        if _is_network_supervisor_compat_transport(state) and _compat_suppress_passive_rag(state)[0]:
            prepared_messages = messages
            passive_rag_duration_ms = 0.0
            _suppress_rag, rag_skip_reason = _compat_suppress_passive_rag(state)
            passive_rag_diagnostics = {
                "injection_allowed": False,
                "reject_reason": rag_skip_reason or "compat_classifier_suppressed_passive_rag",
                "compatIngressFiltering": True,
            }
        else:
            passive_rag_started_at = time.perf_counter()
            prepared_messages = apply_passive_rag_injection(
                messages,
                user_query=user_query,
                scope_chain=scope_chain,
                memory_runtime=memory_runtime,
            )
            passive_rag_duration_ms = round((time.perf_counter() - passive_rag_started_at) * 1000, 2)
            passive_rag_diagnostics = _last_human_memory_rag_diagnostics(prepared_messages)
        log_memory_observation(
            "passive_rag",
            "SUCCESS" if passive_rag_diagnostics.get("injection_allowed") else "SKIPPED",
            trigger="supervisor_turn",
            sessionId=session_id,
            runId=state.get("run_id") or state.get("runId"),
            callsLlm=False,
            scope=current_scope,
            scopeChain=scope_chain,
            durationMs=passive_rag_duration_ms,
            topScores=passive_rag_diagnostics.get("top_scores") or [],
            threshold=passive_rag_diagnostics.get("threshold"),
            rejectReason=passive_rag_diagnostics.get("reject_reason") or None,
            injected=bool(passive_rag_diagnostics.get("injection_allowed")),
            hasRecallCue=bool(passive_rag_diagnostics.get("has_recall_cue")),
            humanTurns=passive_rag_diagnostics.get("human_turns"),
        )
        prepared_messages = prepare_supervisor_messages(
            messages=prepared_messages,
            system_content=system_content,
            prompt_segments=context_bundle.get("v8_prompt_segments") or [],
            ensure_reasoning_content=ensure_reasoning_content,
            sanitize_message_chain=sanitize_message_chain,
            context_orchestrator=context_orchestrator,
            resolved_model_id=sup_model_name,
            resolved_scope=current_scope,
            scope_chain=scope_chain,
            remaining_steps=state.get("remaining_steps", 100),
        )
        if _should_force_memory_broker_first(
            user_query=user_query,
            passive_rag_diagnostics=passive_rag_diagnostics,
            selected_tools=filtered_supervisor_tools,
            state=state,
        ):
            prepared_messages.append(_memory_broker_first_guidance(user_query))
        spec_guidance = _spec_mode_stage_guidance(
            state=state,
            user_query=user_query,
            selected_tools=filtered_supervisor_tools,
            messages=prepared_messages,
        )
        if spec_guidance is not None:
            prepared_messages.append(spec_guidance)
        if fast_first_turn_route:
            prepared_messages.append(_fast_first_turn_guidance())
        if _runtime_episode_handoff_ready(state):
            filtered_supervisor_tools = []
            prepared_messages.append(_runtime_handoff_final_message())
        elif _runtime_episode_recoverable_failure(state):
            dispatch_status = dict((state or {}).get("planner_dispatch_status") or {})
            failure_reason = str(dispatch_status.get("reason") or dispatch_status.get("state") or "runtime_episode_failed").strip()
            if not _has_runtime_recoverable_failure_message(prepared_messages, failure_reason):
                prepared_messages.append(_runtime_recoverable_failure_message(state))
        extensions_runtime_service.emit_supervisor_diagnostics(
            {
                "queryPreview": str(user_query or "")[:160],
                "routeBuildMs": route_duration_ms,
                "systemContentBuildMs": prompt_duration_ms,
                "passiveRagMs": passive_rag_duration_ms,
                "selectedSkillCount": len(route_bundle.selected_skill_names or []),
                "selectedMcpToolCount": len(route_bundle.exposed_mcp_tool_names or []),
                "selectedPluginHostToolCount": len(route_bundle.candidate_summary.get("pluginHostTools") or []),
                "extensionsPrefilterPromptIncluded": include_extensions_prefilter_prompt,
                "fastFirstTurnRoute": fast_first_turn_route,
                "routeReason": route_bundle.candidate_summary.get("reason"),
                "scope": current_scope,
                "sessionId": session_id,
                "runtimeReflex": reflex_decision.as_dict(),
                "runtimeGate": gate_decision.as_dict(),
                "evidenceFeedback": evidence_feedback_packet.as_dict(),
            }
        )

        debug_supervisor_messages(prepared_messages)
        invoke_llm = supervisor_base_llm
        invoke_caller_kwargs = caller_kwargs
        if fast_first_turn_route:
            invoke_caller_kwargs = _fast_first_turn_caller_kwargs(caller_kwargs)
            try:
                invoke_llm = llm_factory.create_chat_model(
                    sup_model_name,
                    streaming=False,
                    _role="supervisor",
                    **invoke_caller_kwargs,
                )
            except Exception:
                invoke_llm = supervisor_base_llm
                invoke_caller_kwargs = caller_kwargs
        response = robust_invoke(
            invoke_llm,
            prepared_messages,
            filtered_supervisor_tools,
            role="supervisor",
            preferred_model_id=sup_model_name,
            build_model=lambda candidate_model_id: llm_factory.create_chat_model(
                candidate_model_id,
                streaming=False,
                _role="supervisor",
                **invoke_caller_kwargs,
            ),
        )
        response = sanitize_response_tool_calls(response)
        response = _coerce_recoverable_failure_response(response, state)
        _attach_route_context_to_response(
            response,
            user_query=user_query,
            route_bundle=route_bundle,
            selected_tools=filtered_supervisor_tools,
        )
        extensions_runtime_service.emit_response_tool_calls(response)
        response, loop_breaker = apply_no_progress_breaker(prepared_messages, response)
        extensions_runtime_service.emit_execution_completed(response=response)
    finally:
        extensions_runtime_service.reset_execution_context(route_context_token)
    if loop_breaker is not None:
        tool_list = ", ".join(loop_breaker["tool_names"])
        print(
            f"[LoopBreaker] Short-circuited repeated tool cycle ({tool_list}) "
            f"x{loop_breaker['count']} with identical observation fingerprint"
        )
    return response
