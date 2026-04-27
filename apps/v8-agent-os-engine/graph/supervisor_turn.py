import time

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


def _attach_route_context_to_response(response, *, user_query: str, route_bundle, selected_tools) -> None:
    payload = {
        "query": user_query,
        "selectedSkillIds": list(route_bundle.selected_skill_ids or []),
        "selectedSkillNames": list(route_bundle.selected_skill_names or []),
        "selectedSkillEntries": list(route_bundle.candidate_summary.get("skillEntries") or []),
        "skillRootDescriptors": list(route_bundle.skill_root_descriptors or []),
        "selectedMcpTools": list(route_bundle.exposed_mcp_tool_names or []),
        "selectedPluginHostTools": list(route_bundle.candidate_summary.get("pluginHostTools") or []),
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
    context_info = resolve_supervisor_request_context(messages, scope_resolution_service)
    user_query = context_info["user_query"]
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
        route_started_at = time.perf_counter()
        route_bundle = extensions_runtime_service.build_supervisor_route(
            user_query=user_query,
            supervisor_tools=visible_supervisor_tools,
            loaded_agents=loaded_agents,
        )
        route_duration_ms = round((time.perf_counter() - route_started_at) * 1000, 2)
        filtered_supervisor_tools = route_bundle.filtered_tools
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
            ensure_reasoning_content=ensure_reasoning_content,
            sanitize_message_chain=sanitize_message_chain,
            context_orchestrator=context_orchestrator,
            resolved_model_id=sup_model_name,
            resolved_scope=current_scope,
            scope_chain=scope_chain,
            remaining_steps=state.get("remaining_steps", 100),
        )
        extensions_runtime_service.emit_supervisor_diagnostics(
            {
                "queryPreview": str(user_query or "")[:160],
                "routeBuildMs": route_duration_ms,
                "systemContentBuildMs": prompt_duration_ms,
                "passiveRagMs": passive_rag_duration_ms,
                "selectedSkillCount": len(route_bundle.selected_skill_names or []),
                "selectedMcpToolCount": len(route_bundle.exposed_mcp_tool_names or []),
                "selectedPluginHostToolCount": len(route_bundle.candidate_summary.get("pluginHostTools") or []),
                "scope": current_scope,
                "sessionId": session_id,
                "runtimeReflex": reflex_decision.as_dict(),
                "runtimeGate": gate_decision.as_dict(),
                "evidenceFeedback": evidence_feedback_packet.as_dict(),
            }
        )

        debug_supervisor_messages(prepared_messages)
        response = robust_invoke(
            supervisor_base_llm,
            prepared_messages,
            filtered_supervisor_tools,
            role="supervisor",
            preferred_model_id=sup_model_name,
            build_model=lambda candidate_model_id: llm_factory.create_chat_model(
                candidate_model_id,
                streaming=True,
                _role="supervisor",
                **caller_kwargs,
            ),
        )
        response = sanitize_response_tool_calls(response)
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
