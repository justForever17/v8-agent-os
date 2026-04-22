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
from runtimes.network_supervisor.openai_compat import (
    build_engine_chat_request_from_openai,
    build_external_tool_alias_maps,
    build_openai_completion_response,
    extract_bearer_token,
    wire_tool_call_id,
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


def _sse_frame(payload: dict[str, object] | str) -> bytes:
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n".encode("utf-8")


async def _stream_openai_chat_completion(
    request_payload: dict[str, object],
    *,
    chat_request,
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
        run_id = f"run_{uuid.uuid4().hex}"
        events: list[dict[str, Any]] = []
        emitted_role = False
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
                if not emitted_role:
                    yield _sse_frame(
                        {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": chat_request.config.model_name,
                            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                        }
                    )
                    emitted_role = True
                if event_type == "text_chunk":
                    content = str(event.get("content") or "")
                    if content:
                        yield _sse_frame(
                            {
                                "id": response_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": chat_request.config.model_name,
                                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                            }
                        )
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
                    tool_index = len(emitted_tool_call_ids)
                    emitted_tool_call_ids.add(wire_id)
                    tool_calls_seen = True
                    args_payload = tool_payload.get("args")
                    if isinstance(args_payload, str):
                        arguments = args_payload
                    else:
                        arguments = json.dumps(args_payload or {}, ensure_ascii=False)
                    yield _sse_frame(
                        {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": chat_request.config.model_name,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": tool_index,
                                                "id": wire_id,
                                                "type": "function",
                                                "function": {"name": wire_name, "arguments": arguments},
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                    )
                    continue
                if event_type == "done":
                    finish_reason = "tool_calls" if tool_calls_seen or str(event.get("status") or "").strip() == "tool_calls_requested" else "stop"
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
                    yield _sse_frame(
                        {
                            "id": response_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": chat_request.config.model_name,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                        }
                    )
                    yield _sse_frame("[DONE]")
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
            yield _sse_frame(
                {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": chat_request.config.model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                }
            )
            yield _sse_frame("[DONE]")
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
    compat_config = network_supervisor_service.get_config_model().openai_compat
    try:
        chat_request = build_engine_chat_request_from_openai(
            payload,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_hint=scope_hint,
            scope_mode=scope_mode,
            max_external_tools=int(compat_config.max_external_tools or 8),
            max_external_system_tokens=int(compat_config.max_external_system_tokens or 1200),
            max_external_message_tokens=int(compat_config.max_external_message_tokens or 16000),
            max_external_tool_description_tokens=int(compat_config.max_external_tool_description_tokens or 800),
            max_external_tool_schema_bytes=int(compat_config.max_external_tool_schema_bytes or 32768),
            max_external_tools_payload_tokens=int(compat_config.max_external_tools_payload_tokens or 6000),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if bool(payload.get("stream")):
        return await _stream_openai_chat_completion(
            payload,
            chat_request=chat_request,
            project_id=project_id,
            workspace_id=workspace_id,
            scope_hint=scope_hint,
            external_thread_id=external_thread_id,
            external_user_id=external_user_id,
        )

    run_id = f"run_{uuid.uuid4().hex}"
    events: list[dict[str, Any]] = []
    async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_openai", run_id=run_id):
        if isinstance(event, dict) and str(event.get("type") or "").strip() == "error":
            raise HTTPException(status_code=500, detail=str(event.get("error") or "OpenAI compat execution failed"))
        if isinstance(event, dict):
            events.append(event)
    response_payload = build_openai_completion_response(
        response_id=f"chatcmpl-{uuid.uuid4().hex}",
        model_name=chat_request.config.model_name,
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
