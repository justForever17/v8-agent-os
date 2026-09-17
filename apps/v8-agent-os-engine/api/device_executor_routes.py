"""Exact human management and independent device ingress; no Admin proxy."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from core.auth_context import require_client_principal, revalidate_client_principal
from core.client_identity import IdentityError
from runtimes.network_supervisor.executors.protocol import ExecutorError, MAX_FRAME, canonical, parse, require
from runtimes.network_supervisor.executors.service import get_executor_service

router = APIRouter(tags=["device-executors"])


async def body(request: Request) -> dict:
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        require(len(data) <= MAX_FRAME, "frame_too_large", 413)
    return parse(bytes(data))


def human(request: Request):
    principal = require_client_principal(request)
    revalidate_client_principal(principal)
    require(principal.device_kind in {"human_phone", "local_client"}, "human_principal_required", 403)
    return principal


def error(exc):
    return JSONResponse({"ok": False, "code": exc.code}, status_code=exc.status)


async def control_text(ws: WebSocket) -> str:
    frame = await ws.receive()
    if frame["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(frame.get("code", 1000))
    require(isinstance(frame.get("text"), str), "executor_text_frame_required", 400)
    return frame["text"]


@router.post("/api/client/executors/tickets")
async def ticket(request: Request, principal=Depends(human)):
    try:
        data = await body(request)
        return get_executor_service().identities.ticket(principal.subject, device_class=data.get("deviceClass"),
                                                       name=data.get("name", ""), base_url=data.get("baseUrl", ""))
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.post("/api/executor/enroll")
async def enroll(request: Request):
    try:
        return get_executor_service().enroll(await body(request))
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.post("/api/executor/revoke")
def revoke_self(request: Request):
    try:
        service = get_executor_service()
        authorization = request.headers.get("authorization", "")
        require(authorization.startswith("Bearer "), "executor_credential_required", 401)
        principal = service.identities.verify(authorization.removeprefix("Bearer "))
        service.revoke(principal["owner_id"], principal["device_id"])
        return {"ok": True, "revoked": True}
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.get("/api/client/executors")
def devices(principal=Depends(human)):
    return {"items": get_executor_service().list(principal.subject)}


@router.put("/api/client/executors/{device_id}/grants")
async def grant(device_id: str, request: Request, principal=Depends(human)):
    try:
        data = await body(request)
        return get_executor_service().grant(principal.subject, device_id, data.get("expectedRevision"), data.get("grants"))
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.delete("/api/client/executors/{device_id}")
def revoke(device_id: str, principal=Depends(human)):
    try:
        get_executor_service().revoke(principal.subject, device_id)
        return {"ok": True, "deviceId": device_id, "revoked": True}
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.get("/api/client/executors/commands/{command_id}")
def command(command_id: str, principal=Depends(human)):
    try:
        return get_executor_service().status(principal.subject, command_id)
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.post("/api/client/executors/commands/{command_id}/cancel")
def cancel(command_id: str, principal=Depends(human)):
    try:
        return get_executor_service().cancel(principal.subject, command_id)
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.post("/api/client/executors/commands/{command_id}/reconcile")
async def reconcile(command_id: str, request: Request, principal=Depends(human)):
    try:
        data = await body(request)
        return get_executor_service().reconcile(principal.subject, command_id, data.get("note"))
    except (ExecutorError, IdentityError) as exc:
        return error(exc)


@router.websocket("/api/executor/ws")
async def executor_socket(ws: WebSocket):
    service, device, epoch = get_executor_service(), "", 0
    receive = None
    try:
        # No query tokens, redirects, client JWTs or internal management proof.
        require(not ws.url.query, "executor_query_not_allowed", 401)
        authorization = ws.headers.get("authorization", "")
        require(authorization.startswith("Bearer "), "executor_credential_required", 401)
        credential = authorization.removeprefix("Bearer ")
        principal = service.identities.verify(credential)
        await ws.accept(subprotocol="v8.device-executor.v1" if "v8.device-executor.v1" in ws.scope.get("subprotocols", []) else None)
        hello = parse(await asyncio.wait_for(control_text(ws), 10))
        session = service.hello(principal, hello)
        device, epoch = principal["device_id"], session["leaseEpoch"]
        await ws.send_text(canonical(session))
        for query in service.queries(device):
            await ws.send_text(canonical(query))
        receive = asyncio.create_task(control_text(ws))
        last_ping, window, count = time.monotonic(), time.monotonic(), 0
        sent_controls = set()
        while True:
            # Verification runs during an established socket as well as upgrade.
            service.identities.verify(credential)
            require(time.monotonic() - last_ping < 30, "executor_idle_timeout")
            for control in service.controls(device, epoch):
                if control["commandId"] not in sent_controls:
                    await ws.send_text(canonical(control))
                    sent_controls.add(control["commandId"])
            for outbound in service.outbound(device, epoch):
                await ws.send_text(canonical(outbound))
            done, _ = await asyncio.wait({receive}, timeout=0.2)
            if not done:
                continue
            received = receive
            receive = None
            message = parse(received.result())
            if time.monotonic() - window >= 1:
                window, count = time.monotonic(), 0
            count += 1
            require(count <= 30, "executor_rate_exceeded", 429)
            if message.get("type") == "ping":
                last_ping = time.monotonic()
                await ws.send_text(canonical(service.renew(device, epoch)))
            elif message.get("type") == "receipt":
                result = service.receipt(device, epoch, message)
                await ws.send_text(canonical({"type": "receipt_ack", "commandId": result["commandId"], "receiptSeq": message["receiptSeq"]}))
            elif message.get("type") == "stopped":
                break
            else:
                raise ExecutorError("executor_message_unsupported", 400)
            receive = asyncio.create_task(control_text(ws))
    except (ExecutorError, IdentityError) as exc:
        await ws.close(code=4401 if exc.status in (401, 403) else 4409, reason=exc.code[:100])
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        if receive is not None:
            receive.cancel()
            try:
                await receive
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
        if device:
            service.disconnect(device, epoch)
