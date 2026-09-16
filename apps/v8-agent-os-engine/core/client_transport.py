"""In-process ASGI dispatch for the authenticated human-client API.

There is no HTTP upstream and no privileged credential manufactured for the
caller. Only the client adapter chooses the target route; the verified context
travels in ASGI state and legacy business headers are derived from that context.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from starlette.responses import Response


def _target_app(request: Request, path: str):
    if path in {"/audio/input-status", "/audio/stt/transcribe", "/audio/tts/stream"}:
        app = getattr(request.app.state, "client_audio_router", None)
        if app is None:
            raise HTTPException(503, "audio_runtime_unavailable")
        return app, "/v1" + path
    app = getattr(request.app.state, "client_internal_router", None)
    if app is None:
        raise HTTPException(503, "client_engine_router_unavailable")
    return app, path


def internal_scope(request: Request, principal: Any, path: str, *, method: str | None = None,
                   query: dict | None = None, body: bytes | None = None) -> dict:
    scope = dict(request.scope)
    scope.update(path=path, raw_path=path.encode(), root_path="", path_params={},
                 method=method or request.method)
    scope["state"] = {**request.scope.get("state", {}), "engine_auth_context": principal}
    if query is not None:
        scope["query_string"] = urlencode(query, doseq=True).encode()
    headers = [(key, value) for key, value in request.scope.get("headers", [])
               if not key.lower().startswith(b"x-v8-")
               and key.lower() not in {b"authorization", b"cookie", b"content-length"}]
    # These two headers are deliberately user-facing interaction hints used by
    # upload/background handlers.  Preserve only this narrow allowlist; all
    # service/auth headers remain stripped so a client cannot impersonate an
    # internal caller. Session binding is added below only after the adapter
    # has checked its owner and recorded the canonical session in state.
    for name in ("x-v8-upload-mode", "x-v8-background-intent"):
        value = request.headers.get(name)
        if value:
            headers.append((name.encode(), value.encode()))
    headers.extend([(b"x-v8-agent-os-user-email", principal.session_id.encode()),
                    (b"x-v8-agent-os-user-id", principal.subject.encode())])
    # The value binds a pagination cursor to the verified Engine instance.
    headers.append((b"x-v8-authority-instance-id", str(principal.issuer).encode()))
    if path.startswith("/audio/"):
        # The audio provider gate is an Engine-internal capability check. Keep
        # the service credential inside this process; never copy it from a
        # client header or expose it in the client request context.
        from core.system_base import get_internal_secret
        secret = str(get_internal_secret() or "")
        if secret:
            headers.append((b"x-v8-agent-os-secret", secret.encode()))
    session_id = request.scope.get("state", {}).get("client_session_id")
    if session_id:
        headers.append((b"x-v8-session-id", str(session_id).encode()))
    if request.headers.get("origin"):
        headers.append((b"x-v8-terminal-origin", request.headers["origin"].encode()))
    if body is not None:
        headers.append((b"content-length", str(len(body)).encode()))
    scope["headers"] = headers
    return scope


class InternalResponse(Response):
    """Forward ASGI frames without buffering SSE, files, or disconnects."""
    def __init__(self, request: Request, principal: Any, path: str, *, method: str | None = None,
                 query: dict | None = None, body: bytes | None = None):
        super().__init__(content=b"")
        self.request, self.principal, self.path = request, principal, path
        self.method, self.query, self.input_body = method, query, body

    async def __call__(self, scope, receive, send):
        app, path = _target_app(self.request, self.path)
        sent = False

        async def receive_body():
            nonlocal sent
            if self.input_body is not None and not sent:
                sent = True
                return {"type": "http.request", "body": self.input_body, "more_body": False}
            return await receive()

        await app(internal_scope(self.request, self.principal, path,
                                 method=self.method, query=self.query, body=self.input_body),
                  receive_body, send)


async def internal_json(request: Request, principal: Any, path: str, *, method: str = "GET",
                        query: dict | None = None, payload: dict | None = None,
                        raw_body: bytes | None = None) -> Any:
    """Call a finite JSON business handler, retaining its actual failure status."""
    app, path = _target_app(request, path)
    body = raw_body if raw_body is not None else json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
    scope = internal_scope(request, principal, path, method=method, query=query or {}, body=body)
    if raw_body is None:
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"content-type"]
        scope["headers"].append((b"content-type", b"application/json"))
    status, chunks = 500, []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    try:
        value = json.loads(b"".join(chunks))
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(502, "client_engine_invalid_json") from exc
    if status >= 400:
        raise HTTPException(status, value.get("detail", value) if isinstance(value, dict) else value)
    return value
