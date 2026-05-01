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
    build_anthropic_message_response,
    build_engine_chat_request_from_anthropic,
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
    build_openai_completion_response,
    extract_bearer_token,
    normalize_openai_compat_model_aliases,
    resolve_openai_compat_model_alias,
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


def _anthropic_sse_frame(event: str, payload: dict[str, object]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


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
                            "model": response_model_name,
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
                                "model": response_model_name,
                                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                            }
                        )
                    continue
                if event_type == "reasoning_chunk":
                    content = str(event.get("content") or "")
                    if content:
                        yield _sse_frame(
                            {
                                "id": response_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": response_model_name,
                                "choices": [{"index": 0, "delta": {"reasoning_content": content}, "finish_reason": None}],
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
                            "model": response_model_name,
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
                            "model": response_model_name,
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
                "model": response_model_name,
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


async def _stream_anthropic_message(
    request_payload: dict[str, object],
    *,
    chat_request,
    response_model_name: str,
    include_thinking: bool,
) -> StreamingResponse:
    response_id = f"msg_{uuid.uuid4().hex}"
    _wire_to_internal, internal_to_wire = build_external_tool_alias_maps(chat_request.config.external_tools)

    async def _generator():
        run_id = f"run_{uuid.uuid4().hex}"
        next_block_index = 0
        open_blocks: set[int] = set()
        active_block_index: int | None = None
        active_block_type = ""
        tool_uses_seen = False

        def _close_active_block_frames() -> list[str]:
            nonlocal active_block_index, active_block_type
            if active_block_index is None:
                return []
            block_index = active_block_index
            active_block_index = None
            active_block_type = ""
            open_blocks.discard(block_index)
            return [_anthropic_sse_frame("content_block_stop", {"type": "content_block_stop", "index": block_index})]

        def _ensure_active_block_frames(block_type: str, content_block: dict[str, Any]) -> tuple[list[str], int]:
            nonlocal next_block_index, active_block_index, active_block_type
            frames: list[str] = []
            if active_block_index is not None and active_block_type == block_type:
                return frames, active_block_index
            frames.extend(_close_active_block_frames())
            block_index = next_block_index
            next_block_index += 1
            active_block_index = block_index
            active_block_type = block_type
            open_blocks.add(block_index)
            frames.append(
                _anthropic_sse_frame(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": content_block,
                    },
                )
            )
            return frames, block_index

        yield _anthropic_sse_frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": response_id,
                    "type": "message",
                    "role": "assistant",
                    "model": response_model_name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        try:
            async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_anthropic", run_id=run_id):
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type") or "").strip()
                if event_type == "error":
                    raise RuntimeError(str(event.get("error") or "Anthropic compat execution failed"))
                if event_type == "reasoning_chunk" and include_thinking:
                    content = str(event.get("content") or "")
                    if not content:
                        continue
                    frames, block_index = _ensure_active_block_frames(
                        "thinking",
                        {"type": "thinking", "thinking": "", "signature": ""},
                    )
                    for frame in frames:
                        yield frame
                    yield _anthropic_sse_frame(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": block_index, "delta": {"type": "thinking_delta", "thinking": content}},
                    )
                    continue
                if event_type == "text_chunk":
                    content = str(event.get("content") or "")
                    if not content:
                        continue
                    frames, block_index = _ensure_active_block_frames("text", {"type": "text", "text": ""})
                    for frame in frames:
                        yield frame
                    yield _anthropic_sse_frame(
                        "content_block_delta",
                        {"type": "content_block_delta", "index": block_index, "delta": {"type": "text_delta", "text": content}},
                    )
                    continue
                if event_type == "tool_start":
                    for frame in _close_active_block_frames():
                        yield frame
                    tool_payload = dict(event.get("tool") or {})
                    internal_name = str(tool_payload.get("toolName") or "").strip()
                    wire_name = internal_to_wire.get(internal_name)
                    if not wire_name:
                        continue
                    tool_uses_seen = True
                    block_index = next_block_index
                    next_block_index += 1
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
                    yield _anthropic_sse_frame(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "tool_use", "id": wire_id, "name": wire_name, "input": {}},
                        },
                    )
                    yield _anthropic_sse_frame(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {"type": "input_json_delta", "partial_json": json.dumps(parsed_args or {}, ensure_ascii=False)},
                        },
                    )
                    yield _anthropic_sse_frame("content_block_stop", {"type": "content_block_stop", "index": block_index})
                    continue
                if event_type == "done":
                    for frame in _close_active_block_frames():
                        yield frame
                    for block_index in sorted(open_blocks):
                        yield _anthropic_sse_frame("content_block_stop", {"type": "content_block_stop", "index": block_index})
                    open_blocks.clear()
                    stop_reason = "tool_use" if tool_uses_seen or str(event.get("status") or "").strip() == "tool_calls_requested" else "end_turn"
                    yield _anthropic_sse_frame(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                            "usage": {"output_tokens": 0},
                        },
                    )
                    yield _anthropic_sse_frame("message_stop", {"type": "message_stop"})
                    return
            for frame in _close_active_block_frames():
                yield frame
            for block_index in sorted(open_blocks):
                yield _anthropic_sse_frame("content_block_stop", {"type": "content_block_stop", "index": block_index})
            stop_reason = "tool_use" if tool_uses_seen else "end_turn"
            yield _anthropic_sse_frame(
                "message_delta",
                {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 0}},
            )
            yield _anthropic_sse_frame("message_stop", {"type": "message_stop"})
        except Exception as exc:
            yield _anthropic_sse_frame(
                "error",
                {"type": "error", "error": {"type": "api_error", "message": str(exc)}},
            )

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

    run_id = f"run_{uuid.uuid4().hex}"
    events: list[dict[str, Any]] = []
    async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_openai", run_id=run_id):
        if isinstance(event, dict) and str(event.get("type") or "").strip() == "error":
            raise HTTPException(status_code=500, detail=str(event.get("error") or "OpenAI compat execution failed"))
        if isinstance(event, dict):
            events.append(event)
    response_payload = build_openai_completion_response(
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
        )

    run_id = f"run_{uuid.uuid4().hex}"
    events: list[dict[str, Any]] = []
    async for event in chat_runtime.stream_legacy_events(chat_request, transport="network_supervisor_anthropic", run_id=run_id):
        if isinstance(event, dict) and str(event.get("type") or "").strip() == "error":
            raise HTTPException(status_code=500, detail=str(event.get("error") or "Anthropic compat execution failed"))
        if isinstance(event, dict):
            events.append(event)
    return JSONResponse(
        build_anthropic_message_response(
            response_id=f"msg_{uuid.uuid4().hex}",
            model_name=response_model_name,
            events=events,
            external_tools=chat_request.config.external_tools,
            include_thinking=include_thinking,
        )
    )
