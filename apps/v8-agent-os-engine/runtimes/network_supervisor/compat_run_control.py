"""Optional compat run handles; identity and continuation stay in existing ERC owners.

Ordinary OpenAI requests remain stateless. A human pause returns an explicit
handle so clients can inspect/answer/cancel the original run without replaying it.
API keys cannot approve local Safety requests through this extension.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from core.database import db


def compat_owner(origin_token_hash: str) -> str:
    return "network_compat_" + origin_token_hash


def remember_context(request: Any, *, run_id: str, protocol: str, origin_token_hash: str, external_thread_id: str | None) -> None:
    from erc.run_service import run_service
    if not origin_token_hash or not db.get_run_record(run_id):
        return
    run_service.update_metadata(run_id, {"compatContext": {
        "protocol": protocol, "originTokenHash": origin_token_hash, "externalThreadId": external_thread_id,
        "externalTools": [tool.model_dump(by_alias=True) for tool in request.config.external_tools or []],
        "ingressDiagnostics": {key: value for key, value in dict(getattr(request.data, "compat_ingress_diagnostics", None) or {}).items()
                               if key in {"protocol", "compatContextMode", "compatExecutionPolicy", "externalToolsPrimary", "suppressPassiveRag", "suppressExtensionsPrefilter", "v8MainChainMode"}},
    }})


def resume_data(record: dict[str, Any]) -> Any:
    from api.models import ChatRequestData
    context = dict((record.get("metadata") or {}).get("compatContext") or {})
    origin = str(context.get("originTokenHash") or "")
    if not origin or record.get("user_id") != compat_owner(origin):
        return None
    diagnostics = dict(context.get("ingressDiagnostics") or {})
    diagnostics["protocol"] = str(context.get("protocol") or "openai")
    diagnostics["suppressExtensionsPrefilter"] = True
    return ChatRequestData(disableExtensionsPrefilter=True, compatIngressDiagnostics=diagnostics)


def run_surface(run_id: str, *, fallback_status: str = "unknown", session_id: str = "") -> dict[str, Any]:
    record = db.get_run_record(run_id) or {}
    session = str(record.get("session_id") or session_id)
    result: dict[str, Any] = {"runId": run_id, "sessionId": session,
                              "status": str(record.get("status") or fallback_status)}
    if not record:
        return result
    result["questions"] = [{"interactionId": row["id"], "question": row.get("question") or row.get("prompt"),
                            "questions": dict(row.get("request") or {}).get("questions") or []}
                           for row in db.list_ask_user_interactions(run_id=run_id, status="pending")]
    result["approvals"] = [{"approvalId": row["id"], "kind": row.get("approval_kind"), "resolveAt": "V8OS approval UI"}
                           for row in db.list_pending_approvals(run_id=run_id, status="pending")]
    message = db.get_chat_canonical_message_by_run(session_id=session, run_id=run_id)
    if message:
        result["content"] = str(message.get("content_text") or "")
    context = dict((record.get("metadata") or {}).get("compatContext") or {})
    if context.get("originTokenHash"):
        from runtimes.network_supervisor.service import network_supervisor_service
        result["toolResultReceipts"] = [network_supervisor_service.external_tool_receipt_status(item)
            for item in network_supervisor_service.pending_external_tools_snapshot().values()
            if item.get("originTokenHash") == context["originTokenHash"] and item.get("runId") == run_id and item.get("receiptId")]
    if result["status"] == "waiting_external_tool" and context.get("originTokenHash"):
        from api.models import ExternalToolSpec
        from runtimes.network_supervisor.openai_compat import extract_external_tool_calls_from_events, build_external_tool_alias_maps
        from runtimes.network_supervisor.anthropic_compat import extract_anthropic_tool_use_blocks_from_events
        from runtimes.network_supervisor.service import network_supervisor_service
        specs = [ExternalToolSpec.model_validate(tool) for tool in context.get("externalTools") or []]
        wire_to_internal, internal_to_wire = build_external_tool_alias_maps(specs)
        events = db.get_runtime_events_for_run(run_id, session_id=session)
        proof = next((row for row in reversed(events) if row.get("topic") == "network.external_tool.waiting"), {})
        tool_call_id = str((proof.get("payload") or {}).get("toolCallId") or "")
        # tool.started alone can precede a Safety rejection. Only the exact call
        # named by the current durable runtime handoff may reach the client.
        last_boundary = next((row for row in reversed(events) if row.get("topic") in {"network.external_tool.waiting", "approval.requested", "ask_user.requested", "run.cancelled", "run.failed"}), {})
        if not tool_call_id or last_boundary.get("id") != proof.get("id"):
            result["deliveryError"] = "external_tool_handoff_proof_missing"
            return result
        starts = [{"type": "tool_start", "tool": (row.get("payload") or {}).get("tool") or {}}
                  for row in events if row.get("topic") == "tool.started"
                  and str(((row.get("payload") or {}).get("tool") or {}).get("toolCallId") or "") == tool_call_id
                  and int(row.get("seq") or 0) < int(proof.get("seq") or 0)
                  and internal_to_wire.get(str(((row.get("payload") or {}).get("tool") or {}).get("toolName") or ""))]
        protocol = str(context.get("protocol") or "openai")
        calls = (extract_anthropic_tool_use_blocks_from_events(starts[-1:], external_tools=specs) if protocol == "anthropic"
                 else extract_external_tool_calls_from_events(starts[-1:], external_tools=specs))
        available_calls = []
        for call in calls:
            name = str(call.get("name") or (call.get("function") or {}).get("name") or "")
            network_supervisor_service.record_pending_external_tool(
                protocol=protocol, origin_token_hash=context["originTokenHash"], run_id=run_id,
                compat_session_id=session, external_thread_id=context.get("externalThreadId"),
                wire_tool_call_id=call["id"], internal_alias_name=wire_to_internal[name], external_wire_name=name,
                checkpoint_resume_supported=True,
            )
            key = network_supervisor_service._external_tool_pending_key(protocol, call["id"], compat_session_id=session)
            state = network_supervisor_service.pending_external_tools_snapshot()
            if (state.get(key) or {}).get("status") == "waiting_external_tool":
                available_calls.append(call)
        result["tool_uses" if protocol == "anthropic" else "tool_calls"] = available_calls
    return result


def control_request(payload: dict[str, Any], *, origin_token_hash: str) -> dict[str, Any] | None:
    control = payload.get("v8os_control")
    if control is None:
        return None
    if not isinstance(control, dict) or set(control) - {"runId", "action", "interactionId", "answer"}:
        raise HTTPException(400, "Invalid v8os_control fields")
    run_id = str(control.get("runId") or "")
    record = db.get_run_record(run_id)
    if not record or str(record.get("user_id") or "") != compat_owner(origin_token_hash):
        raise HTTPException(404, "Compat run not found for this API identity")
    action = str(control.get("action") or "status")
    from erc.command_router import runtime_command_router
    from erc.models import RuntimeCommand
    if action == "answer":
        interaction_id = str(control.get("interactionId") or "")
        interaction = db.get_ask_user_interaction(interaction_id)
        if not interaction or str(interaction.get("run_id")) != run_id:
            raise HTTPException(404, "Question does not belong to this run")
        if str(interaction.get("status")) != "pending" or str(record.get("status")) != "waiting_input":
            raise HTTPException(409, "Question is no longer awaiting an answer")
        answer = control.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise HTTPException(400, "A nonempty answer is required")
        if runtime_command_router._schedule_chat_run is None:
            raise HTTPException(503, "Chat continuation scheduler is unavailable")
        runtime_command_router.dispatch_ask_user_command(RuntimeCommand(
            topic="ask_user.respond", interaction_id=interaction_id,
            response={"interactionId": interaction_id, "answer": answer},
        ))
    elif action == "cancel":
        runtime_command_router.dispatch_run_command(RuntimeCommand(topic="run.cancel", run_id=run_id, reason="compat_client_cancel"))
    elif action != "status":
        raise HTTPException(400, "Supported actions are status, answer and cancel; Safety approval remains in V8OS UI")
    return run_surface(run_id)
