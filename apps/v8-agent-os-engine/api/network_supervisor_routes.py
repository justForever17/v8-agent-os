from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from core.system_base import get_internal_secret
from runtimes.chat.runtime import chat_runtime
from runtimes.network_supervisor.models import (
    NetworkDelegationRequestPayload,
    NetworkDiagnosticsPayload,
    NetworkEnvelope,
    NetworkPeerMutationPayload,
)
from runtimes.network_supervisor.anthropic_compat import (
    ANTHROPIC_COMPAT_MIN_EXTERNAL_SYSTEM_TOKENS,
    ANTHROPIC_COMPAT_MIN_EXTERNAL_TOOLS,
    ANTHROPIC_COMPAT_MIN_EXTERNAL_TOOLS_PAYLOAD_TOKENS,
    anthropic_wire_tool_use_id,
    build_anthropic_compat_models_response,
    build_engine_chat_request_from_anthropic,
    extract_anthropic_tool_use_blocks_from_events,
    extract_anthropic_api_key,
    wants_anthropic_thinking,
)
from runtimes.network_supervisor.openai_compat import (
    COMPAT_MAX_EXTERNAL_MESSAGE_TOKENS,
    COMPAT_MAX_EXTERNAL_PAYLOAD_TOKENS,
    COMPAT_MAX_EXTERNAL_SYSTEM_TOKENS,
    COMPAT_MAX_EXTERNAL_TOOL_DESCRIPTION_TOKENS,
    COMPAT_MAX_EXTERNAL_TOOL_SCHEMA_BYTES,
    COMPAT_MAX_EXTERNAL_TOOLS,
    build_openai_compat_models_response,
    build_engine_chat_request_from_openai,
    build_external_tool_alias_maps,
    extract_bearer_token,
    extract_external_tool_calls_from_events,
    normalize_openai_compat_model_aliases,
    resolve_openai_compat_model_alias,
    wire_tool_call_id,
)
from runtimes.network_supervisor.compat_wire_emitter import (
    AnthropicStreamTimelineEmitter,
    OpenAIStreamTimelineEmitter,
    compat_wire_emitter,
)
from runtimes.network_supervisor.memory_adapter import network_supervisor_memory_adapter
from runtimes.network_supervisor.service import network_supervisor_service


router = APIRouter()


def _verify_admin_relay_secret(secret: str | None) -> None:
    expected = str(get_internal_secret() or "").strip()
    if not expected:
        raise HTTPException(status_code=500, detail="Internal relay secret is not configured")
    if str(secret or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid internal relay secret")


def _resolve_openai_scope_headers(request: Request) -> tuple[str | None, str | None, str | None, str]:
    config = network_supervisor_service.get_config_model().openai_compat
    raw_workspace_path = request.headers.get("x-v8-workspace-path")
    if raw_workspace_path and not config.allow_raw_workspace_path:
        raise HTTPException(status_code=400, detail="Raw workspace path headers are not allowed")

    project_id = str(request.headers.get("x-v8-project-id") or "").strip() or None
    workspace_id = str(request.headers.get("x-v8-workspace-id") or "").strip() or None
    scope_hint = str(request.headers.get("x-v8-scope-hint") or "").strip() or None
    if not config.allow_workspace_headers and any([project_id, workspace_id, scope_hint]):
        raise HTTPException(status_code=403, detail="Workspace headers are disabled for OpenAI compat")
    return project_id, workspace_id, scope_hint, str(config.default_scope_mode or "explicit").strip() or "explicit"


def _resolve_openai_external_headers(request: Request) -> tuple[str | None, str | None]:
    external_thread_id = str(request.headers.get("x-v8-external-thread-id") or "").strip() or None
    external_user_id = str(request.headers.get("x-v8-external-user-id") or "").strip() or None
    return external_thread_id, external_user_id


def _record_openai_memory_adapter_status(result: dict[str, Any]) -> None:
    try:
        network_supervisor_service.record_openai_compat_memory_adapter_status(result)
    except Exception:
        # Diagnostics must never break the OpenAI-compatible wire path.
        pass


def _event_status(event: dict[str, Any]) -> str:
    return str((event or {}).get("status") or "").strip().lower()


def _is_waiting_approval_event(event: dict[str, Any]) -> bool:
    return str((event or {}).get("type") or "").strip() == "done" and _event_status(event) == "waiting_approval"


def _approval_notice_text(event: dict[str, Any], *, run_id: str) -> str:
    payload = dict((event or {}).get("payload") or {})
    approval_ref = str(
        payload.get("approval_id")
        or payload.get("approvalId")
        or payload.get("approvalRef")
        or ""
    ).strip()
    parts = [
        "V8OS 需要在 Admin Operations Center 完成人工审批后继续。",
        f"runId={str((event or {}).get('run_id') or run_id).strip() or run_id}",
    ]
    if approval_ref:
        parts.append(f"approvalRef={approval_ref}")
    parts.append("审批完成后，外部客户端可继续发送下一轮消息，V8OS 会尝试恢复该 run。")
    return "\n".join(parts)


def _openai_tool_result_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in list(payload.get("messages") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "tool":
            continue
        wire_id = str(item.get("tool_call_id") or item.get("toolCallId") or "").strip()
        if wire_id:
            ids.append(wire_id)
    return ids


def _openai_tool_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in list(payload.get("messages") or []):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "tool":
            continue
        wire_id = str(item.get("tool_call_id") or item.get("toolCallId") or "").strip()
        if not wire_id:
            continue
        results.append(
            {
                "protocol": "openai",
                "wireToolCallId": wire_id,
                "name": str(item.get("name") or "").strip(),
                "content": item.get("content"),
            }
        )
    return results


def _anthropic_tool_result_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for message in list(payload.get("messages") or []):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() != "tool_result":
                continue
            wire_id = str(block.get("tool_use_id") or "").strip()
            if wire_id:
                ids.append(wire_id)
    return ids


def _anthropic_tool_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for message in list(payload.get("messages") or []):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() != "tool_result":
                continue
            wire_id = str(block.get("tool_use_id") or "").strip()
            if not wire_id:
                continue
            results.append(
                {
                    "protocol": "anthropic",
                    "wireToolCallId": wire_id,
                    "toolUseId": wire_id,
                    "content": block.get("content"),
                    "isError": bool(block.get("is_error") or block.get("isError")),
                }
            )
    return results


def _apply_external_tool_resume_claim(chat_request, claim: dict[str, Any] | None) -> None:
    claim = dict(claim or {})
    resume_run_id = str(claim.get("resumeRunId") or "").strip()
    resume_value = claim.get("resumeValue")
    if resume_run_id and isinstance(resume_value, dict):
        chat_request.resume_run_id = resume_run_id
        chat_request.resume_value = resume_value
    elif claim.get("pendingMissReason"):
        try:
            diagnostics = dict((chat_request.data.compat_ingress_diagnostics if chat_request.data else {}) or {})
            diagnostics["pendingMissReason"] = claim.get("pendingMissReason")
            diagnostics["unmatchedExternalToolIds"] = list(claim.get("unmatchedIds") or [])
            if chat_request.data:
                chat_request.data.compat_ingress_diagnostics = diagnostics
        except Exception:
            pass


async def _stream_openai_chat_completion(
    request_payload: dict[str, object],
    *,
    chat_request,
    response_model_name: str,
    project_id: str | None,
    workspace_id: str | None,
    scope_hint: str | None,
    external_thread_id: str | None,
    external_user_id: str | None,
) -> StreamingResponse:
    response_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    _wire_to_internal, internal_to_wire = build_external_tool_alias_maps(chat_request.config.external_tools)

    async def _generator():
        run_id = chat_request.resume_run_id or f"run_{uuid.uuid4().hex}"
        events: list[dict[str, Any]] = []
        emitter = OpenAIStreamTimelineEmitter(response_id=response_id, model_name=response_model_name, created=created)
        emitted_tool_call_ids: set[str] = set()
        tool_calls_seen = False
        try:
            async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_openai", run_id=run_id):
                if not isinstance(event, dict):
                    continue
                events.append(event)
                event_type = str(event.get("type") or "").strip()
                if event_type == "error":
                    message = str(event.get("error") or "OpenAI compat execution failed")
                    failed = {
                        "adapterStatus": "failed",
                        "reason": message,
                        "sourceRuntime": "network_supervisor",
                        "provenanceClass": "external_api_dialogue",
                        "memoryPolicy": "compat_minimal",
                        "externalThreadId": external_thread_id,
                        "externalUserId": external_user_id,
                        "projectId": project_id,
                        "workspaceId": workspace_id,
                        "scopeHint": scope_hint,
                    }
                    _record_openai_memory_adapter_status(failed)
                    raise RuntimeError(message)
                if event_type == "text_chunk":
                    content = str(event.get("content") or "")
                    for frame in emitter.text_delta(content):
                        yield frame
                    continue
                if event_type == "reasoning_chunk":
                    content = str(event.get("content") or "")
                    for frame in emitter.reasoning_delta(content):
                        yield frame
                    continue
                if event_type == "tool_start":
                    tool_payload = dict(event.get("tool") or {})
                    internal_name = str(tool_payload.get("toolName") or "").strip()
                    wire_name = internal_to_wire.get(internal_name)
                    if not wire_name:
                        continue
                    internal_tool_call_id = str(tool_payload.get("toolCallId") or "").strip()
                    wire_id = wire_tool_call_id(internal_tool_call_id, wire_name=wire_name)
                    if wire_id in emitted_tool_call_ids:
                        continue
                    network_supervisor_service.record_pending_external_tool(
                        protocol="openai",
                        run_id=run_id,
                        wire_tool_call_id=wire_id,
                        internal_alias_name=internal_name,
                        external_wire_name=wire_name,
                        compat_session_id=chat_request.session_id,
                        external_thread_id=external_thread_id,
                        external_user_id=external_user_id,
                    )
                    tool_index = len(emitted_tool_call_ids)
                    emitted_tool_call_ids.add(wire_id)
                    tool_calls_seen = True
                    args_payload = tool_payload.get("args")
                    if isinstance(args_payload, str):
                        arguments = args_payload
                    else:
                        arguments = json.dumps(args_payload or {}, ensure_ascii=False)
                    for frame in emitter.tool_call_delta(
                        index=tool_index,
                        wire_id=wire_id,
                        wire_name=wire_name,
                        arguments=arguments,
                    ):
                        yield frame
                    continue
                if event_type == "done":
                    if _is_waiting_approval_event(event):
                        notice = _approval_notice_text(event, run_id=run_id)
                        for frame in emitter.approval_notice(notice):
                            yield frame
                        response_payload = {"choices": [{"finish_reason": "stop"}], "v8os_status": "waiting_approval"}
                        result = network_supervisor_memory_adapter.record_openai_compat_delta(
                            payload=request_payload,
                            chat_request=chat_request,
                            run_id=run_id,
                            events=events,
                            response_payload=response_payload,
                            project_id=project_id,
                            workspace_id=workspace_id,
                            scope_hint=scope_hint,
                            external_thread_id=external_thread_id,
                            external_user_id=external_user_id,
                        )
                        _record_openai_memory_adapter_status(result)
                        for frame in emitter.finish("stop"):
                            yield frame
                        return
                    finish_reason = "tool_calls" if tool_calls_seen or str(event.get("status") or "").strip() in {"tool_calls_requested", "waiting_external_tool"} else "stop"
                    response_payload = {"choices": [{"finish_reason": finish_reason}]}
                    result = network_supervisor_memory_adapter.record_openai_compat_delta(
                        payload=request_payload,
                        chat_request=chat_request,
                        run_id=run_id,
                        events=events,
                        response_payload=response_payload,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        scope_hint=scope_hint,
                        external_thread_id=external_thread_id,
                        external_user_id=external_user_id,
                    )
                    _record_openai_memory_adapter_status(result)
                    for frame in emitter.finish(finish_reason):
                        yield frame
                    return
            finish_reason = "tool_calls" if tool_calls_seen else "stop"
            response_payload = {"choices": [{"finish_reason": finish_reason}]}
            result = network_supervisor_memory_adapter.record_openai_compat_delta(
                payload=request_payload,
                chat_request=chat_request,
                run_id=run_id,
                events=events,
                response_payload=response_payload,
                project_id=project_id,
                workspace_id=workspace_id,
                scope_hint=scope_hint,
                external_thread_id=external_thread_id,
                external_user_id=external_user_id,
            )
            _record_openai_memory_adapter_status(result)
            for frame in emitter.finish(finish_reason):
                yield frame
        except Exception as exc:
            failed = {
                "adapterStatus": "failed",
                "reason": str(exc),
                "sourceRuntime": "network_supervisor",
                "provenanceClass": "external_api_dialogue",
                "memoryPolicy": "compat_minimal",
                "externalThreadId": external_thread_id,
                "externalUserId": external_user_id,
                "projectId": project_id,
                "workspaceId": workspace_id,
                "scopeHint": scope_hint,
            }
            _record_openai_memory_adapter_status(failed)
            raise

    return StreamingResponse(_generator(), media_type="text/event-stream; charset=utf-8")


async def _stream_anthropic_message(
    request_payload: dict[str, object],
    *,
    chat_request,
    response_model_name: str,
    include_thinking: bool,
    external_thread_id: str | None = None,
    external_user_id: str | None = None,
) -> StreamingResponse:
    response_id = f"msg_{uuid.uuid4().hex}"
    _wire_to_internal, internal_to_wire = build_external_tool_alias_maps(chat_request.config.external_tools)

    async def _generator():
        run_id = chat_request.resume_run_id or f"run_{uuid.uuid4().hex}"
        emitter = AnthropicStreamTimelineEmitter(response_id=response_id, model_name=response_model_name)
        tool_uses_seen = False
        yield emitter.message_start()
        try:
            async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_anthropic", run_id=run_id):
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "").strip()
                if event_type == "error":
                    raise RuntimeError(str(event.get("error") or "Anthropic compat execution failed"))
                if event_type == "reasoning_chunk" and include_thinking:
                    content = str(event.get("content") or "")
                    for frame in emitter.thinking_delta(content):
                        yield frame
                    continue
                if event_type == "text_chunk":
                    content = str(event.get("content") or "")
                    for frame in emitter.text_delta(content):
                        yield frame
                    continue
                if event_type == "tool_start":
                    tool_payload = dict(event.get("tool") or {})
                    internal_name = str(tool_payload.get("toolName") or "").strip()
                    wire_name = internal_to_wire.get(internal_name)
                    if not wire_name:
                        continue
                    tool_uses_seen = True
                    args_payload = tool_payload.get("args")
                    if isinstance(args_payload, str):
                        try:
                            parsed_args = json.loads(args_payload)
                        except Exception:
                            parsed_args = {"input": args_payload}
                    elif isinstance(args_payload, dict):
                        parsed_args = args_payload
                    else:
                        parsed_args = {}
                    wire_id = anthropic_wire_tool_use_id(str(tool_payload.get("toolCallId") or "").strip(), wire_name=wire_name)
                    network_supervisor_service.record_pending_external_tool(
                        protocol="anthropic",
                        run_id=run_id,
                        wire_tool_call_id=wire_id,
                        internal_alias_name=internal_name,
                        external_wire_name=wire_name,
                        compat_session_id=chat_request.session_id,
                        external_thread_id=external_thread_id,
                        external_user_id=external_user_id,
                    )
                    for frame in emitter.tool_use(wire_id=wire_id, wire_name=wire_name, input_payload=parsed_args):
                        yield frame
                    continue
                if event_type == "done":
                    if _is_waiting_approval_event(event):
                        notice = _approval_notice_text(event, run_id=run_id)
                        for frame in emitter.approval_notice(notice):
                            yield frame
                        for frame in emitter.finish("end_turn"):
                            yield frame
                        return
                    stop_reason = "tool_use" if tool_uses_seen or str(event.get("status") or "").strip() in {"tool_calls_requested", "waiting_external_tool"} else "end_turn"
                    for frame in emitter.finish(stop_reason):
                        yield frame
                    return
            stop_reason = "tool_use" if tool_uses_seen else "end_turn"
            for frame in emitter.finish(stop_reason):
                yield frame
        except Exception as exc:
            yield emitter.error(str(exc))

    return StreamingResponse(_generator(), media_type="text/event-stream; charset=utf-8")


@router.get("/network-supervisor/status")
async def get_network_supervisor_status():
    return network_supervisor_service.status_payload()


@router.get("/network-supervisor/openai/compat/tokens")
async def get_network_supervisor_openai_compat_tokens(
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    return network_supervisor_service.list_openai_compat_tokens()


@router.post("/network-supervisor/openai/compat/tokens")
async def post_network_supervisor_openai_compat_token(
    payload: dict[str, Any] | None = None,
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    body = dict(payload or {})
    return network_supervisor_service.create_openai_compat_token(label=str(body.get("label") or "").strip() or None)


@router.delete("/network-supervisor/openai/compat/tokens/{token_id}")
async def delete_network_supervisor_openai_compat_token(
    token_id: str,
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    return network_supervisor_service.delete_openai_compat_token(token_id)


@router.get("/network-supervisor/peers")
async def get_network_supervisor_peers():
    return network_supervisor_service.list_peers_payload()


@router.post("/network-supervisor/peers")
async def post_network_supervisor_peer(payload: NetworkPeerMutationPayload):
    return network_supervisor_service.upsert_peer(payload)


@router.patch("/network-supervisor/peers/{peer_id}")
async def patch_network_supervisor_peer(peer_id: str, payload: dict):
    body = dict(payload or {})
    body["peerId"] = peer_id
    return network_supervisor_service.upsert_peer(NetworkPeerMutationPayload.model_validate(body))


@router.delete("/network-supervisor/peers/{peer_id}")
async def delete_network_supervisor_peer(peer_id: str):
    return network_supervisor_service.delete_peer(peer_id)


@router.post("/network-supervisor/diagnostics/challenge")
async def post_network_supervisor_diagnostics_challenge(payload: NetworkDiagnosticsPayload):
    return await network_supervisor_service.challenge_peer(payload.peer_id, note=payload.note)


@router.post("/network-supervisor/diagnostics/wake")
async def post_network_supervisor_diagnostics_wake(payload: NetworkDiagnosticsPayload):
    return await network_supervisor_service.wake_peer(payload.peer_id, note=payload.note, delegation_hint=payload.task)


@router.post("/network-supervisor/delegations")
async def post_network_supervisor_delegation(payload: NetworkDelegationRequestPayload):
    return await network_supervisor_service.delegate_task(
        peer_id=payload.peer_id,
        task=payload.task,
        timeout_seconds=payload.timeout_seconds,
        project_id=payload.project_id,
        workspace_id=payload.workspace_id,
        workspace_path=payload.workspace_path,
        scope_hint=payload.scope_hint,
    )


@router.get("/network-supervisor/delegations/{delegation_id}")
async def get_network_supervisor_delegation(delegation_id: str):
    return network_supervisor_service.get_delegation(delegation_id)


@router.post("/network-supervisor/peer/join")
async def post_network_supervisor_peer_join(
    envelope: NetworkEnvelope,
    x_v8_peer_token: str | None = Header(default=None, alias="X-V8-Peer-Token"),
):
    network_supervisor_service.verify_inbound_peer_token(x_v8_peer_token)
    response = network_supervisor_service.handle_peer_join_request(envelope)
    return response.model_dump(by_alias=True)


@router.post("/network-supervisor/peer/challenge")
async def post_network_supervisor_peer_challenge(
    envelope: NetworkEnvelope,
    x_v8_peer_token: str | None = Header(default=None, alias="X-V8-Peer-Token"),
):
    network_supervisor_service.verify_inbound_peer_token(x_v8_peer_token)
    response = network_supervisor_service.handle_peer_challenge_request(envelope)
    return response.model_dump(by_alias=True)


@router.post("/network-supervisor/peer/wake")
async def post_network_supervisor_peer_wake(
    envelope: NetworkEnvelope,
    x_v8_peer_token: str | None = Header(default=None, alias="X-V8-Peer-Token"),
):
    network_supervisor_service.verify_inbound_peer_token(x_v8_peer_token)
    response = network_supervisor_service.handle_peer_wake_request(envelope)
    return response.model_dump(by_alias=True)


@router.post("/network-supervisor/peer/delegations")
async def post_network_supervisor_peer_delegations(
    envelope: NetworkEnvelope,
    x_v8_peer_token: str | None = Header(default=None, alias="X-V8-Peer-Token"),
):
    network_supervisor_service.verify_inbound_peer_token(x_v8_peer_token)
    response = await network_supervisor_service.handle_peer_delegations(envelope)
    return response.model_dump(by_alias=True)


@router.websocket("/network-supervisor/peer/ws")
async def network_supervisor_peer_ws(websocket: WebSocket):
    token = websocket.headers.get("x-v8-peer-token")
    try:
        network_supervisor_service.verify_inbound_peer_token(token)
    except HTTPException:
        await websocket.close(code=4401)
        return
    await network_supervisor_service.websocket_handshake(websocket)


@router.get("/network-supervisor/openai/models")
async def get_network_supervisor_openai_models(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    bearer_token = extract_bearer_token(authorization)
    network_supervisor_service.verify_openai_compat_token(bearer_token)
    compat_config = network_supervisor_service.get_config_model().openai_compat
    return build_openai_compat_models_response(compat_config.model_aliases)


@router.get("/network-supervisor/anthropic/v1/models")
@router.get("/network-supervisor/anthropic/models")
async def get_network_supervisor_anthropic_models(
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    network_supervisor_service.verify_openai_compat_token(extract_anthropic_api_key(authorization, x_api_key))
    compat_config = network_supervisor_service.get_config_model().openai_compat
    return build_anthropic_compat_models_response(compat_config.model_aliases)


@router.post("/network-supervisor/openai/chat/completions")
async def post_network_supervisor_openai_chat_completions(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    bearer_token = extract_bearer_token(authorization)
    network_supervisor_service.verify_openai_compat_token(bearer_token)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    project_id, workspace_id, scope_hint, scope_mode = _resolve_openai_scope_headers(request)
    external_thread_id, external_user_id = _resolve_openai_external_headers(request)
    external_tool_claim = network_supervisor_service.claim_external_tool_results(
        protocol="openai",
        wire_tool_call_ids=_openai_tool_result_ids(payload),
        tool_results=_openai_tool_results(payload),
        external_thread_id=external_thread_id,
    )
    compat_config = network_supervisor_service.get_config_model().openai_compat
    aliases = normalize_openai_compat_model_aliases(compat_config.model_aliases)
    try:
        response_model_name = resolve_openai_compat_model_alias(payload.get("model"), aliases)
        chat_request = build_engine_chat_request_from_openai(
            payload,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_hint=scope_hint,
            scope_mode=scope_mode,
            model_name_override="gpt-4o",
            max_external_tools=max(int(compat_config.max_external_tools or 8), COMPAT_MAX_EXTERNAL_TOOLS),
            max_external_system_tokens=max(
                int(compat_config.max_external_system_tokens or 1200),
                COMPAT_MAX_EXTERNAL_SYSTEM_TOKENS,
            ),
            max_external_message_tokens=max(
                int(compat_config.max_external_message_tokens or 16000),
                COMPAT_MAX_EXTERNAL_MESSAGE_TOKENS,
            ),
            max_external_tool_description_tokens=max(
                int(compat_config.max_external_tool_description_tokens or 800),
                COMPAT_MAX_EXTERNAL_TOOL_DESCRIPTION_TOKENS,
            ),
            max_external_tool_schema_bytes=max(
                int(compat_config.max_external_tool_schema_bytes or 32768),
                COMPAT_MAX_EXTERNAL_TOOL_SCHEMA_BYTES,
            ),
            max_external_tools_payload_tokens=max(
                int(compat_config.max_external_tools_payload_tokens or 6000),
                COMPAT_MAX_EXTERNAL_PAYLOAD_TOKENS,
            ),
        )
        _apply_external_tool_resume_claim(chat_request, external_tool_claim)
    except ValueError as exc:
        status_code = 404 if "Unknown V8OS OpenAI-compatible model alias" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    if bool(payload.get("stream")):
        return await _stream_openai_chat_completion(
            payload,
            chat_request=chat_request,
            response_model_name=response_model_name,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_hint=scope_hint,
            external_thread_id=external_thread_id,
            external_user_id=external_user_id,
        )

    run_id = chat_request.resume_run_id or f"run_{uuid.uuid4().hex}"
    events: list[dict[str, Any]] = []
    async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_openai", run_id=run_id):
        if isinstance(event, dict) and str(event.get("type") or "").strip() == "error":
            raise HTTPException(status_code=500, detail=str(event.get("error") or "OpenAI compat execution failed"))
        if isinstance(event, dict):
            events.append(event)
    wire_to_internal, _internal_to_wire = build_external_tool_alias_maps(chat_request.config.external_tools)
    for tool_call in extract_external_tool_calls_from_events(events, external_tools=chat_request.config.external_tools):
        function_payload = dict(tool_call.get("function") or {})
        external_wire_name = str(function_payload.get("name") or "").strip()
        network_supervisor_service.record_pending_external_tool(
            protocol="openai",
            run_id=run_id,
            wire_tool_call_id=str(tool_call.get("id") or "").strip(),
            internal_alias_name=wire_to_internal.get(external_wire_name, external_wire_name),
            external_wire_name=external_wire_name,
            compat_session_id=chat_request.session_id,
            external_thread_id=external_thread_id,
            external_user_id=external_user_id,
        )
    approval_event = next((event for event in reversed(events) if isinstance(event, dict) and _is_waiting_approval_event(event)), None)
    if approval_event:
        response_payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response_model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": _approval_notice_text(approval_event, run_id=run_id)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "v8os_status": "waiting_approval",
        }
    else:
        response_payload = compat_wire_emitter.openai_chat_completion(
            response_id=f"chatcmpl-{uuid.uuid4().hex}",
            model_name=response_model_name,
            events=events,
            external_tools=chat_request.config.external_tools,
        )
    adapter_result = network_supervisor_memory_adapter.record_openai_compat_delta(
        payload=payload,
        chat_request=chat_request,
        run_id=run_id,
        events=events,
        response_payload=response_payload,
        project_id=project_id,
        workspace_id=workspace_id,
        scope_hint=scope_hint,
        external_thread_id=external_thread_id,
        external_user_id=external_user_id,
    )
    _record_openai_memory_adapter_status(adapter_result)
    return JSONResponse(response_payload)


@router.post("/network-supervisor/anthropic/v1/messages")
@router.post("/network-supervisor/anthropic/messages")
async def post_network_supervisor_anthropic_messages(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
    x_v8_agent_os_secret: str | None = Header(default=None, alias="X-V8-Agent-OS-Secret"),
):
    _verify_admin_relay_secret(x_v8_agent_os_secret)
    network_supervisor_service.verify_openai_compat_token(extract_anthropic_api_key(authorization, x_api_key))
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    project_id, workspace_id, scope_hint, scope_mode = _resolve_openai_scope_headers(request)
    external_thread_id, external_user_id = _resolve_openai_external_headers(request)
    external_tool_claim = network_supervisor_service.claim_external_tool_results(
        protocol="anthropic",
        wire_tool_call_ids=_anthropic_tool_result_ids(payload),
        tool_results=_anthropic_tool_results(payload),
        external_thread_id=external_thread_id,
    )
    compat_config = network_supervisor_service.get_config_model().openai_compat
    aliases = normalize_openai_compat_model_aliases(compat_config.model_aliases)
    try:
        response_model_name = resolve_openai_compat_model_alias(payload.get("model"), aliases)
        chat_request = build_engine_chat_request_from_anthropic(
            payload,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_hint=scope_hint,
            scope_mode=scope_mode,
            model_name_override="gpt-4o",
            max_external_tools=max(
                int(compat_config.max_external_tools or 8),
                ANTHROPIC_COMPAT_MIN_EXTERNAL_TOOLS,
            ),
            max_external_system_tokens=max(
                int(compat_config.max_external_system_tokens or 1200),
                ANTHROPIC_COMPAT_MIN_EXTERNAL_SYSTEM_TOKENS,
            ),
            max_external_message_tokens=max(
                int(compat_config.max_external_message_tokens or 16000),
                COMPAT_MAX_EXTERNAL_MESSAGE_TOKENS,
            ),
            max_external_tool_description_tokens=max(
                int(compat_config.max_external_tool_description_tokens or 800),
                COMPAT_MAX_EXTERNAL_TOOL_DESCRIPTION_TOKENS,
            ),
            max_external_tool_schema_bytes=max(
                int(compat_config.max_external_tool_schema_bytes or 32768),
                COMPAT_MAX_EXTERNAL_TOOL_SCHEMA_BYTES,
            ),
            max_external_tools_payload_tokens=max(
                int(compat_config.max_external_tools_payload_tokens or 6000),
                ANTHROPIC_COMPAT_MIN_EXTERNAL_TOOLS_PAYLOAD_TOKENS,
            ),
        )
        _apply_external_tool_resume_claim(chat_request, external_tool_claim)
    except ValueError as exc:
        status_code = 404 if "Unknown V8OS OpenAI-compatible model alias" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    include_thinking = wants_anthropic_thinking(payload)
    if bool(payload.get("stream")):
        return await _stream_anthropic_message(
            payload,
            chat_request=chat_request,
            response_model_name=response_model_name,
            include_thinking=include_thinking,
            external_thread_id=external_thread_id,
            external_user_id=external_user_id,
        )

    run_id = chat_request.resume_run_id or f"run_{uuid.uuid4().hex}"
    events: list[dict[str, Any]] = []
    async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_anthropic", run_id=run_id):
        if isinstance(event, dict) and str(event.get("type") or "").strip() == "error":
            raise HTTPException(status_code=500, detail=str(event.get("error") or "Anthropic compat execution failed"))
        if isinstance(event, dict):
            events.append(event)
    wire_to_internal, _internal_to_wire = build_external_tool_alias_maps(chat_request.config.external_tools)
    for tool_use in extract_anthropic_tool_use_blocks_from_events(events, external_tools=chat_request.config.external_tools):
        external_wire_name = str(tool_use.get("name") or "").strip()
        network_supervisor_service.record_pending_external_tool(
            protocol="anthropic",
            run_id=run_id,
            wire_tool_call_id=str(tool_use.get("id") or "").strip(),
            internal_alias_name=wire_to_internal.get(external_wire_name, external_wire_name),
            external_wire_name=external_wire_name,
            compat_session_id=chat_request.session_id,
            external_thread_id=external_thread_id,
            external_user_id=external_user_id,
        )
    approval_event = next((event for event in reversed(events) if isinstance(event, dict) and _is_waiting_approval_event(event)), None)
    if approval_event:
        return JSONResponse(
            {
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "model": response_model_name,
                "content": [{"type": "text", "text": _approval_notice_text(approval_event, run_id=run_id)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "v8os_status": "waiting_approval",
            }
        )
    return JSONResponse(
        compat_wire_emitter.anthropic_message(
            response_id=f"msg_{uuid.uuid4().hex}",
            model_name=response_model_name,
            events=events,
            external_tools=chat_request.config.external_tools,
            include_thinking=include_thinking,
        )
    )
