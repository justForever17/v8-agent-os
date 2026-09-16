"""Keep private Engine control routes separate from the Phone client surface."""
from __future__ import annotations

import hmac
import re

from starlette.responses import JSONResponse

# These handlers validate their own ticket, OAuth state or signed peer envelope.
# No Phone token authorizes arbitrary /v1 control-plane operations.
_PEER = re.compile(r"/v1/network-supervisor/peer/(?:join|challenge|wake|neighbors/pairing/consume|neighbors/messages|neighbors/tasks|delegations)")
_WS = re.compile(r"/v1/(?:chat/ws|terminal/sessions/[^/]+/ws|workbench/browser-sessions/[^/]+/ws|network-supervisor/peer/ws)")
_INSPECTOR_CALLBACK = re.compile(r"/v1/rpa/recordings/[A-Za-z0-9_-]+/inspector/sessions/[A-Za-z0-9_-]+/events")
_MODEL_READ = re.compile(r"/v1/network-supervisor/(?:openai/models|anthropic/(?:v1/)?models)")
_MODEL_WRITE = re.compile(r"/v1/network-supervisor/(?:openai/chat/completions|anthropic/(?:v1/)?messages)")


class EngineControlBoundary:
    def __init__(self, app, *, secret_reader=None):
        self.app = app
        if secret_reader is None:
            from core.system_base import get_internal_secret
            secret_reader = get_internal_secret
        self.secret_reader = secret_reader

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if scope.get("state", {}).get("local_management") is True:
            return await self.app(scope, receive, send)
        if (not path.startswith(("/v1/", "/workspace/"))
                or path == "/v1/health"
                or (kind == "http" and scope.get("method") == "OPTIONS")
                or (kind == "http" and scope.get("method") == "POST" and _PEER.fullmatch(path))
                or (kind == "http" and scope.get("method") == "GET" and path == "/v1/api/plugins/oauth/callback")
                # The native inspector owns a recording/session-specific token;
                # requiring the service key here would break its governed callback.
                or (kind == "http" and scope.get("method") == "POST" and _INSPECTOR_CALLBACK.fullmatch(path))
                # External model clients have their own managed API token owner;
                # token administration remains on the private control surface.
                or (kind == "http" and scope.get("method") == "GET" and _MODEL_READ.fullmatch(path))
                or (kind == "http" and scope.get("method") == "POST" and _MODEL_WRITE.fullmatch(path))
                or (kind == "websocket" and _WS.fullmatch(path))):
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = headers.get(b"x-v8-agent-os-secret", b"")
        expected = str(self.secret_reader() or "").encode()
        if expected and provided and hmac.compare_digest(expected, provided):
            return await self.app(scope, receive, send)
        if kind == "websocket":
            return await send({"type": "websocket.close", "code": 4401, "reason": "local_management_required"})
        response = JSONResponse({"error": "local_management_required", "code": "auth_pre_execution"}, status_code=401,
                                headers={"X-V8-Auth-Stage": "pre_execution"})
        await response(scope, receive, send)
