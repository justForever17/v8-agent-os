from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, WebSocket

from runtimes.network_supervisor.models import (
    NetworkDelegationRequestPayload,
    NetworkDiagnosticsPayload,
    NetworkEnvelope,
    NetworkPeerMutationPayload,
)
from runtimes.network_supervisor.service import network_supervisor_service


router = APIRouter()


@router.get("/network-supervisor/status")
async def get_network_supervisor_status():
    return network_supervisor_service.status_payload()


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
