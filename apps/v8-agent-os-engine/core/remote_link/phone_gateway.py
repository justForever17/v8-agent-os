from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response


LOGGER = logging.getLogger(__name__)
DEFAULT_PHONE_GATEWAY_PORT = 9532
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}"
_UNSAFE_RAW_PATH_MARKERS = (b"%2f", b"%5c", b"%2e", b"\\")
_SAFE_REQUEST_HEADERS = {
    "accept",
    "authorization",
    "content-type",
    "if-modified-since",
    "if-none-match",
    "last-event-id",
    "range",
    "x-v8-upload-mode",
    "x-v8-background-intent",
    "x-v8-session-id",
}
_MODEL_REQUEST_HEADERS = {
    "x-api-key", "anthropic-version", "anthropic-beta", "x-v8-compat-memory",
    "x-v8-project-id", "x-v8-workspace-id", "x-v8-workspace-path", "x-v8-scope-hint",
    "x-v8-external-thread-id", "x-v8-external-user-id",
}
_SAFE_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
    "x-v8-admin-proxy-ms",
    "x-v8-engine-now",
    "x-v8-payload-bytes",
    "x-accel-buffering",
}


class AuditSink(Protocol):
    def __call__(self, event: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class GatewayRatePolicy:
    requests: int
    window_seconds: float = 60.0


@dataclass(frozen=True)
class PhoneGatewayRoute:
    route_id: str
    pattern: re.Pattern[str]
    methods: frozenset[str]
    auth: Literal["public", "bearer", "peer", "model"] = "bearer"
    max_body_bytes: int = 1024 * 1024
    stream: bool = False
    rate: GatewayRatePolicy = field(default_factory=lambda: GatewayRatePolicy(300))

    def matches(self, path: str, method: str) -> bool:
        return method in self.methods and self.pattern.fullmatch(path) is not None


def _route(
    route_id: str,
    path_pattern: str,
    methods: tuple[str, ...],
    *,
    auth: Literal["public", "bearer", "peer", "model"] = "bearer",
    max_body_bytes: int = 1024 * 1024,
    stream: bool = False,
    requests_per_minute: int = 300,
) -> PhoneGatewayRoute:
    return PhoneGatewayRoute(
        route_id=route_id,
        pattern=re.compile(path_pattern),
        methods=frozenset(method.upper() for method in methods),
        auth=auth,
        max_body_bytes=max_body_bytes,
        stream=stream,
        rate=GatewayRatePolicy(requests=requests_per_minute),
    )


# Only client contracts with native ownership checks and separately authenticated
# peer/model protocols are exposed. Local-session and configuration stay private.
PHONE_GATEWAY_ROUTES: tuple[PhoneGatewayRoute, ...] = (
    _route("executor.enroll", r"/api/executor/enroll", ("POST",), auth="public", max_body_bytes=16384, requests_per_minute=8),
    _route("executor.self-revoke", r"/api/executor/revoke", ("POST",), auth="public", max_body_bytes=16384, requests_per_minute=30),
    # These exact routes authenticate independent executor credentials themselves.
    _route("executor.media.reserve", r"/api/executor/media", ("POST",), auth="public", max_body_bytes=16384, requests_per_minute=60),
    _route("executor.media.upload", r"/api/executor/media/media_[0-9a-f]{32}", ("PUT",), auth="public", max_body_bytes=2097152, requests_per_minute=60),
    _route("executor.media.cancel", r"/api/executor/media/media_[0-9a-f]{32}", ("DELETE",), auth="public", max_body_bytes=1024, requests_per_minute=60),
    _route("executor.tickets", r"/api/client/executors/tickets", ("POST",), max_body_bytes=16384, requests_per_minute=8),
    _route("executor.list", r"/api/client/executors", ("GET",)),
    _route("executor.media.delete", r"/api/client/executors/media/media_[0-9a-f]{32}", ("DELETE",)),
    _route("executor.grants", rf"/api/client/executors/{_SEGMENT}/grants", ("PUT",), max_body_bytes=16384),
    _route("executor.revoke", rf"/api/client/executors/{_SEGMENT}", ("DELETE",)),
    _route("executor.command", rf"/api/client/executors/commands/{_SEGMENT}", ("GET",)),
    _route("executor.control", rf"/api/client/executors/commands/{_SEGMENT}/(?:cancel|reconcile)", ("POST",), max_body_bytes=16384),
    _route("model.openai.models", r"/v1/network-supervisor/openai/models", ("GET",), auth="model"),
    _route("model.openai.chat", r"/v1/network-supervisor/openai/chat/completions", ("POST",), auth="model", stream=True, max_body_bytes=2 * 1024 * 1024),
    _route("model.anthropic.models", r"/v1/network-supervisor/anthropic/(?:v1/)?models", ("GET",), auth="model"),
    _route("model.anthropic.messages", r"/v1/network-supervisor/anthropic/(?:v1/)?messages", ("POST",), auth="model", stream=True, max_body_bytes=2 * 1024 * 1024),
    # The Engine also owns the existing signed peer HTTP ingress.  Phone bearer
    # and local service proof cannot replace the peer token/envelope validation.
    *(_route("peer." + suffix.replace("/", "."),
             "/v1/network-supervisor/peer/" + suffix, ("POST",),
             auth="peer", max_body_bytes=262144)
      for suffix in ("join", "challenge", "wake", "delegations", "neighbors/pairing/consume", "neighbors/messages", "neighbors/tasks")),
    _route("instance", r"/api/client/instance", ("GET",), auth="public", requests_per_minute=60),
    _route(
        "pairing.consume",
        r"/api/client/pairing/consume",
        ("POST",),
        auth="public",
        max_body_bytes=64 * 1024,
        requests_per_minute=8,
    ),
    _route(
        "auth.refresh",
        r"/api/client/auth/refresh",
        ("POST",),
        auth="public",
        max_body_bytes=64 * 1024,
        requests_per_minute=20,
    ),
    _route(
        "auth.logout",
        r"/api/client/auth/logout",
        ("POST",),
        auth="public",
        max_body_bytes=64 * 1024,
        requests_per_minute=20,
    ),
    _route("auth.me", r"/api/client/auth/me", ("GET",)),
    _route("auth.profile", r"/api/client/auth/profile", ("GET", "PATCH")),
    _route("connection", r"/api/client/connection", ("GET",)),
    _route("link.manifest", r"/api/client/link/manifest", ("GET",)),
    _route("supervisor.profile", r"/api/client/supervisor-profile", ("GET",)),
    _route("projects.read", r"/api/client/projects", ("GET", "POST")),
    _route("workspace.presentations", r"/api/client/workspace-presentations", ("GET", "PUT")),
    _route("workspace.folders", r"/api/client/workspace/folders", ("GET", "POST")),
    _route("supervisor.peers", r"/api/client/supervisor-peers", ("GET",)),
    _route("supervisor.peer", rf"/api/client/supervisor-peers/{_SEGMENT}", ("PATCH", "DELETE")),
    _route("supervisor.timeline", rf"/api/client/supervisor-peers/{_SEGMENT}/timeline", ("GET",)),
    _route("config.distribution", r"/api/client/config-distribution", ("GET", "POST"), max_body_bytes=65536),
    _route("config.distribution.target", rf"/api/client/config-distribution/targets/{_SEGMENT}", ("GET",)),
    _route("config.distribution.local-workspace", rf"/api/client/config-distribution/local-workspaces/{_SEGMENT}", ("POST",), max_body_bytes=4096),
    _route("config.distribution.job", rf"/api/client/config-distribution/{_SEGMENT}", ("GET",)),
    _route("config.distribution.action", rf"/api/client/config-distribution/{_SEGMENT}/(?:prepare|confirm|retry|cancel|withdraw)", ("POST",), max_body_bytes=4096),
    _route("conversations", r"/api/client/conversations", ("GET", "POST")),
    _route("conversation", rf"/api/client/conversations/{_SEGMENT}", ("GET", "PATCH", "DELETE")),
    _route("conversation.turns", rf"/api/client/conversations/{_SEGMENT}/turns", ("GET",)),
    _route("conversation.sync", rf"/api/client/conversations/{_SEGMENT}/sync", ("GET",)),
    _route("session.scope", rf"/api/client/sessions/{_SEGMENT}/scope", ("GET", "PUT")),
    _route("session.scope.resolve", rf"/api/client/sessions/{_SEGMENT}/scope/re-resolve", ("POST",)),
    _route("session.processes", rf"/api/client/sessions/{_SEGMENT}/processes", ("GET",)),
    _route("session.workbench.read", rf"/api/client/sessions/{_SEGMENT}/workbench/files/read", ("GET",)),
    _route("conversation.turn-index", rf"/api/client/conversations/{_SEGMENT}/turn-index", ("GET",)),
    _route("realtime.snapshot", rf"/api/client/realtime/sessions/{_SEGMENT}/snapshot", ("GET",)),
    _route(
        "realtime.session",
        rf"/api/client/realtime/sessions/{_SEGMENT}/stream",
        ("GET",),
        stream=True,
        requests_per_minute=30,
    ),
    _route(
        "realtime.activity",
        r"/api/client/realtime/session-activity/stream",
        ("GET",),
        stream=True,
        requests_per_minute=30,
    ),
    # Phone uses /chat for streamed replies and /chat-submit for queued work.
    # Both are owned Engine session contracts and retain their native reply.
    _route("chat.submit", r"/api/client/chat(?:-submit)?", ("POST",), max_body_bytes=2 * 1024 * 1024),
    _route("chat.queue.list", r"/api/client/chat-queue", ("GET",)),
    _route("chat.queue", rf"/api/client/chat-queue/{_SEGMENT}", ("PATCH", "DELETE")),
    _route("chat.queue.promote", rf"/api/client/chat-queue/{_SEGMENT}/promote", ("POST",)),
    _route(
        "run.command",
        rf"/api/client/runs/{_SEGMENT}/commands/(?:interrupt|retry|cancel|resume|pause)",
        ("POST",),
    ),
    _route(
        "attachment.upload",
        r"/api/client/upload",
        ("POST",),
        max_body_bytes=64 * 1024 * 1024,
        requests_per_minute=12,
    ),
    _route("artifacts", r"/api/client/artifacts", ("GET",)),
    _route("artifact", rf"/api/client/artifacts/{_SEGMENT}", ("GET",)),
    _route("artifact.content", rf"/api/client/artifacts/{_SEGMENT}/content", ("GET",), stream=True),
    _route("workspace.resource", r"/api/client/workspace/resource", ("GET", "HEAD"), stream=True),
    _route("workspace.file", r"/api/client/workspace/files/(?:[^/]+/)*[^/]+", ("GET", "HEAD"), stream=True),
    _route("user.asset", rf"/(?:api/client/)?user-assets/(?:avatar|background)/{_SEGMENT}", ("GET", "HEAD"), stream=True),
    _route("user.avatar", r"/api/client/user-avatar-upload", ("POST",), max_body_bytes=8 * 1024 * 1024 + 64 * 1024, requests_per_minute=12),
    _route("user.background", r"/api/client/user-background-upload", ("POST",), max_body_bytes=50 * 1024 * 1024 + 64 * 1024, requests_per_minute=12),
    _route("music", r"/api/client/music", ("GET",)),
    _route("sources", r"/api/client/sources", ("GET",)),
    _route("message.delete", rf"/api/client/messages/{_SEGMENT}", ("DELETE",)),
    _route("approvals", r"/api/client/approvals", ("GET",)),
    _route("approval.approve", rf"/api/client/approvals/{_SEGMENT}/approve", ("POST",)),
    _route("approval.reject", rf"/api/client/approvals/{_SEGMENT}/reject", ("POST",)),
    _route("approval.refresh_spec_review", rf"/api/client/approvals/{_SEGMENT}/refresh-spec-review", ("POST",)),
    _route("ask_user.respond", rf"/api/client/ask-user/{_SEGMENT}/respond", ("POST",)),
    _route("specs", r"/api/client/specs", ("GET",)),
    _route("spec", rf"/api/client/specs/{_SEGMENT}", ("GET",)),
    _route("spec.stage", rf"/api/client/specs/{_SEGMENT}/stages/{_SEGMENT}", ("GET",)),
    _route("spec.stage.approve", rf"/api/client/specs/{_SEGMENT}/stages/{_SEGMENT}/approve", ("POST",)),
    _route("spec.stage.revise", rf"/api/client/specs/{_SEGMENT}/stages/{_SEGMENT}/revise", ("POST",)),
    _route("spec.stage.edit", rf"/api/client/specs/{_SEGMENT}/stages/{_SEGMENT}/edit", ("POST",)),
    _route("commands", r"/api/client/commands", ("GET",)),
    _route("command", rf"/api/client/commands/{_SEGMENT}", ("GET",)),
    _route("skills", r"/api/client/skills/list", ("GET",)),
    _route("plugin.mentions", r"/api/client/plugins/mentions", ("GET",)),
    _route("models.reasoning", r"/api/client/models/supervisor-reasoning-effort", ("GET", "PATCH")),
    _route("memory.session_extraction", r"/api/client/memory/session-extraction", ("POST",)),
    _route("ui.action", rf"/api/client/ui-actions/{_SEGMENT}(?:/submit)?", ("GET", "POST")),
    _route("mcp.resource", r"/api/client/mcp-apps/resources/read", ("GET",)),
    _route("mcp.instance", rf"/api/client/mcp-apps/instances/{_SEGMENT}/(?:rpc|close)", ("POST",)),
    _route("audio.input_status", r"/api/client/audio/input-status", ("GET",)),
    _route("audio.stt", r"/api/client/audio/stt", ("POST",), max_body_bytes=64 * 1024 * 1024, requests_per_minute=20),
    _route(
        "audio.tts",
        r"/api/client/audio/tts",
        ("POST",),
        max_body_bytes=256 * 1024,
        stream=True,
        requests_per_minute=60,
    ),
    _route("bg_process.output", rf"/api/client/bg_processes/{_SEGMENT}", ("GET",)),
    _route("bg_process.input", rf"/api/client/bg_processes/{_SEGMENT}/(?:input|sensitive-input|terminate|resize)", ("POST",), max_body_bytes=64 * 1024),
    _route("rpa.availability", r"/api/client/rpa/availability", ("GET",)),
    _route("rpa.drafts", r"/api/client/rpa/drafts", ("GET", "POST")),
    _route("rpa.draft", rf"/api/client/rpa/drafts/{_SEGMENT}", ("GET", "PATCH", "DELETE")),
    _route("rpa.draft.run", rf"/api/client/rpa/drafts/{_SEGMENT}/(?:prepare|run|export)", ("POST",)),
    _route("rpa.templates", r"/api/client/rpa/templates", ("GET",)),
    _route("rpa.template", rf"/api/client/rpa/templates/{_SEGMENT}", ("GET",)),
    _route("rpa.template.run", rf"/api/client/rpa/templates/{_SEGMENT}/run", ("POST",)),
    _route("rpa.scripts", r"/api/client/rpa/scripts", ("GET",)),
    _route("rpa.compile", rf"/api/client/rpa/compile(?:/{_SEGMENT})?", ("POST",)),
    _route("rpa.run_existing", r"/api/client/rpa/run-existing", ("POST",)),
    _route("runs.list", r"/api/client/runs", ("GET",)),
    _route("run.detail", rf"/api/client/runs/{_SEGMENT}", ("GET",)),
    _route("desktop_live.status", r"/api/client/desktop-live/status", ("GET",)),
    _route("desktop_live.prepare", r"/api/client/desktop-live/prepare", ("POST",)),
    _route("desktop_live.session", rf"/api/client/desktop-live/session(?:/{_SEGMENT})?", ("POST", "DELETE")),
    _route("desktop_live.release", r"/api/client/desktop-live/release", ("POST",)),
    _route("desktop_live.offer", r"/api/client/desktop-live/(?:offer|candidate)", ("POST",)),
    _route("desktop_live.stream", r"/api/client/desktop-live/stream", ("GET",), stream=True),
    _route("terminal.profiles", r"/api/client/terminal/profiles", ("GET",)),
    _route("terminal.sessions", r"/api/client/terminal/sessions", ("GET", "POST")),
    _route("terminal.session", rf"/api/client/terminal/sessions/{_SEGMENT}", ("GET",)),
    _route("terminal.session.action", rf"/api/client/terminal/sessions/{_SEGMENT}/(?:input|terminate|resize|ws-ticket)", ("POST",), max_body_bytes=64 * 1024),
)


@dataclass(frozen=True)
class PhoneGatewayConfig:
    listen_host: str = "0.0.0.0"
    listen_port: int = DEFAULT_PHONE_GATEWAY_PORT
    allowed_origins: tuple[str, ...] = (
        "capacitor://localhost",
        "http://localhost",
        "https://localhost",
    )

    def __post_init__(self) -> None:
        if self.listen_host not in {"0.0.0.0", "127.0.0.1"}:
            raise ValueError("phone_gateway_invalid_listen_host")
        if not 1 <= int(self.listen_port) <= 65535:
            raise ValueError("phone_gateway_invalid_port")
        for origin in self.allowed_origins:
            normalized = str(origin or "").strip().rstrip("/")
            if not normalized or normalized == "*":
                raise ValueError("phone_gateway_origin_must_be_explicit")

class _WindowRateLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 4096) -> None:
        self._clock = clock
        self._max_keys = max(128, int(max_keys))
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def allow(self, key: str, policy: GatewayRatePolicy) -> bool:
        now = self._clock()
        cutoff = now - policy.window_seconds
        async with self._lock:
            events = self._entries.pop(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= policy.requests:
                self._entries[key] = events
                return False
            events.append(now)
            self._entries[key] = events
            while len(self._entries) > self._max_keys:
                self._entries.popitem(last=False)
        return True


def _find_route(path: str, method: str) -> PhoneGatewayRoute | None:
    for route in PHONE_GATEWAY_ROUTES:
        if route.matches(path, method):
            return route
    return None


def _raw_path_is_safe(request: Request) -> bool:
    raw_path = bytes(request.scope.get("raw_path") or b"").lower()
    if any(marker in raw_path for marker in _UNSAFE_RAW_PATH_MARKERS):
        return False
    return ".." not in request.url.path.split("/")


def _bearer_token(request: Request) -> str:
    value = str(request.headers.get("authorization") or "").strip()
    if not value.lower().startswith("bearer "):
        return ""
    return value[7:].strip()


def _rate_identity(request: Request, route: PhoneGatewayRoute, token: str) -> str:
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"token:{digest}:{route.route_id}"
    host = str(request.client.host if request.client else "unknown").strip() or "unknown"
    return f"client:{host}:{route.route_id}"


def _cors_headers(origin: str, allowed_origins: frozenset[str]) -> dict[str, str]:
    normalized = origin.strip().rstrip("/")
    if not normalized or normalized not in allowed_origins:
        return {}
    return {
        "access-control-allow-origin": normalized,
        "access-control-allow-credentials": "false",
        "vary": "Origin",
    }


def _compact_audit(
    *,
    request_id: str,
    route_id: str,
    method: str,
    outcome: str,
    status: int,
    started_at: float,
) -> dict[str, Any]:
    return {
        "event": "phone_gateway.request",
        "requestId": request_id,
        "routeId": route_id,
        "method": method,
        "outcome": outcome,
        "status": int(status),
        "durationMs": max(0, round((time.monotonic() - started_at) * 1000)),
    }


def create_phone_gateway_app(
    config: PhoneGatewayConfig | None = None,
    *,
    client_app: Any | None = None,
    audit_sink: AuditSink | None = None,
    rate_limiter: _WindowRateLimiter | None = None,
) -> FastAPI:
    gateway_config = config or PhoneGatewayConfig()
    allowed_origins = frozenset(origin.rstrip("/") for origin in gateway_config.allowed_origins)
    limiter = rate_limiter or _WindowRateLimiter()
    sink = audit_sink or (lambda event: LOGGER.info("phone gateway request", extra={"v8": event}))
    app = FastAPI(
        title="V8OS Phone Remote Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.phone_gateway_config = gateway_config

    @app.websocket("/api/executor/ws")
    async def executor_channel(websocket: WebSocket):
        # This exact route calls the same native Engine handler. It cannot
        # forward arbitrary paths or inject a human/local management principal.
        if client_app is None:
            await websocket.close(code=1013, reason="engine_unavailable")
            return
        from api.device_executor_routes import executor_socket
        await executor_socket(websocket)

    def emit(event: dict[str, Any]) -> None:
        try:
            sink(event)
        except Exception:
            LOGGER.warning("Phone Gateway audit sink failed", exc_info=True)

    async def reject(
        request: Request,
        *,
        request_id: str,
        route_id: str,
        started_at: float,
        status: int,
        code: str,
    ) -> JSONResponse:
        emit(
            _compact_audit(
                request_id=request_id,
                route_id=route_id,
                method=request.method,
                outcome=code,
                status=status,
                started_at=started_at,
            )
        )
        headers = {"x-v8-request-id": request_id, **_cors_headers(str(request.headers.get("origin") or ""), allowed_origins)}
        return JSONResponse({"error": code, "requestId": request_id}, status_code=status, headers=headers)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def proxy_phone_request(request: Request, path: str) -> Response:
        del path
        started_at = time.monotonic()
        request_id = uuid.uuid4().hex
        origin = str(request.headers.get("origin") or "").strip().rstrip("/")

        if not _raw_path_is_safe(request):
            return await reject(
                request,
                request_id=request_id,
                route_id="unmatched",
                started_at=started_at,
                status=404,
                code="phone_gateway_route_not_allowed",
            )

        requested_method = str(request.headers.get("access-control-request-method") or "").upper()
        method = requested_method if request.method == "OPTIONS" else request.method
        route = _find_route(request.url.path, method)
        if route is None:
            return await reject(
                request,
                request_id=request_id,
                route_id="unmatched",
                started_at=started_at,
                status=404,
                code="phone_gateway_route_not_allowed",
            )

        if origin and origin not in allowed_origins:
            return await reject(
                request,
                request_id=request_id,
                route_id=route.route_id,
                started_at=started_at,
                status=403,
                code="phone_gateway_origin_denied",
            )

        cors = _cors_headers(origin, allowed_origins)
        if request.method == "OPTIONS":
            requested_headers = {
                item.strip().lower()
                for item in str(request.headers.get("access-control-request-headers") or "").split(",")
                if item.strip()
            }
            allowed_headers = _SAFE_REQUEST_HEADERS | (_MODEL_REQUEST_HEADERS if route.auth == "model" else set())
            if not requested_headers.issubset(allowed_headers):
                return await reject(
                    request,
                    request_id=request_id,
                    route_id=route.route_id,
                    started_at=started_at,
                    status=403,
                    code="phone_gateway_cors_headers_denied",
                )
            return Response(
                status_code=204,
                headers={
                    **cors,
                    "access-control-allow-methods": ", ".join(sorted(route.methods)),
                    "access-control-allow-headers": ", ".join(sorted(requested_headers)),
                    "access-control-max-age": "600",
                    "x-v8-request-id": request_id,
                },
            )

        is_peer = route.auth == "peer"
        token = str(request.headers.get("x-v8-peer-token") or "") if is_peer else _bearer_token(request)
        if route.auth == "model" and not token:
            token = str(request.headers.get("x-api-key") or "")
        if is_peer:
            if not request.headers.get("content-type", "").lower().startswith("application/json"):
                return await reject(request, request_id=request_id, route_id=route.route_id,
                    started_at=started_at, status=415, code="peer_json_required")
            if route.route_id != "peer.neighbors.pairing.consume" and not token:
                return await reject(request, request_id=request_id, route_id=route.route_id,
                    started_at=started_at, status=401, code="peer_token_required")
        signed_resource = route.route_id in {"artifact.content", "workspace.resource", "workspace.file", "user.asset"} and bool(request.query_params.get("v8sig"))
        if route.auth == "bearer" and not token and not signed_resource:
            return await reject(
                request,
                request_id=request_id,
                route_id=route.route_id,
                started_at=started_at,
                status=401,
                code="phone_gateway_bearer_required",
            )

        if not await limiter.allow(_rate_identity(request, route, token), route.rate):
            response = await reject(
                request,
                request_id=request_id,
                route_id=route.route_id,
                started_at=started_at,
                status=429,
                code="phone_gateway_rate_limited",
            )
            response.headers["retry-after"] = str(max(1, round(route.rate.window_seconds)))
            return response

        content_length = str(request.headers.get("content-length") or "").strip()
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = -1
            if declared_size < 0 or declared_size > route.max_body_bytes:
                return await reject(
                    request,
                    request_id=request_id,
                    route_id=route.route_id,
                    started_at=started_at,
                    status=413,
                    code="phone_gateway_body_too_large",
                )

        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > route.max_body_bytes:
                return await reject(
                    request,
                    request_id=request_id,
                    route_id=route.route_id,
                    started_at=started_at,
                    status=413,
                    code="phone_gateway_body_too_large",
                )

        upstream_headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in _SAFE_REQUEST_HEADERS
        }
        if is_peer:
            upstream_headers.pop("authorization", None)
            if token:
                upstream_headers["x-v8-peer-token"] = token
        if route.auth == "model":
            for name in _MODEL_REQUEST_HEADERS:
                if request.headers.get(name):
                    upstream_headers[name] = request.headers[name]
        upstream_headers["x-v8-client-surface"] = route.auth if route.auth in {"peer", "model"} else "phone"
        upstream_headers["x-v8-request-id"] = request_id
        upstream_headers["accept-encoding"] = "identity"
        if client_app is None:
            return await reject(
                request,
                request_id=request_id,
                route_id=route.route_id,
                started_at=started_at,
                status=503,
                code="phone_gateway_engine_unavailable",
            )
        return _DirectClientResponse(client_app, request, bytes(body), upstream_headers, cors,
                                     request_id, route.route_id, started_at, emit)

    return app


class _DirectClientResponse(Response):
    def __init__(self, app, request, body, headers, cors, request_id, route_id, started_at, emit):
        super().__init__(b"")
        self.app, self.request, self.input_body = app, request, body
        self.headers_in, self.cors, self.request_id = headers, cors, request_id
        self.route_id, self.started_at, self.emit = route_id, started_at, emit

    async def __call__(self, scope, receive, send):
        direct_scope = dict(self.request.scope)
        direct_scope["headers"] = [(key.lower().encode(), value.encode()) for key, value in self.headers_in.items()]
        direct_scope["state"] = {"client_surface": self.headers_in["x-v8-client-surface"], "phone_gateway": True}
        # The inner app must never mistake the outer FastAPI route for its own.
        direct_scope.pop("endpoint", None)
        direct_scope.pop("route", None)
        direct_scope["path_params"] = {}
        sent, status = False, 500

        async def direct_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": self.input_body, "more_body": False}
            return await receive()

        async def direct_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.decode().lower() in _SAFE_RESPONSE_HEADERS
                           or key.decode().lower() == "x-v8-auth-stage"]
                headers.extend((key.encode(), value.encode()) for key, value in self.cors.items())
                headers.append((b"x-v8-request-id", self.request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(direct_scope, direct_receive, direct_send)
        finally:
            self.emit(_compact_audit(request_id=self.request_id, route_id=self.route_id,
                                     method=self.request.method, outcome="forwarded", status=status,
                                     started_at=self.started_at))


class _OwnedGatewayServer(uvicorn.Server):
    # Engine's top-level runner is the sole process-signal owner.
    def capture_signals(self):
        return nullcontext()


class PhoneGatewayServer:
    """In-process loopback server for the future Cloudflare Tunnel controller."""

    def __init__(
        self,
        config: PhoneGatewayConfig | None = None,
        *,
        app: FastAPI | None = None,
    ) -> None:
        self.config = config or PhoneGatewayConfig()
        self.app = create_phone_gateway_app(self.config, client_app=app)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return bool(self._server and self._server.started and self._task and not self._task.done())

    def status(self) -> dict[str, Any]:
        return {
            "state": "running" if self.running else "stopped",
            "listenOrigin": f"http://{self.config.listen_host}:{self.config.listen_port}",
            "upstreamKind": "engine_in_process",
            "routeCount": len(PHONE_GATEWAY_ROUTES),
        }

    async def start(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        if self.running:
            return self.status()
        uvicorn_config = uvicorn.Config(
            self.app,
            host=self.config.listen_host,
            port=self.config.listen_port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            timeout_graceful_shutdown=1.0,
        )
        self._server = _OwnedGatewayServer(uvicorn_config)
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while not self._server.started:
            if self._task.done():
                await self._task
                raise RuntimeError("phone_gateway_start_failed")
            if time.monotonic() >= deadline:
                await self.stop(timeout_seconds=0.5)
                raise TimeoutError("phone_gateway_start_timeout")
            await asyncio.sleep(0.01)
        return self.status()

    async def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        if self._server is not None:
            self._server.config.timeout_graceful_shutdown = max(0.05, min(2.0, timeout_seconds / 2))
            self._server.should_exit = True
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=max(0.5, timeout_seconds))
            except TimeoutError:
                if self._server is not None:
                    self._server.force_exit = True
                    for connection in list(self._server.server_state.connections):
                        connection.shutdown()
                    for request_task in list(self._server.server_state.tasks):
                        request_task.cancel()
                self._task.cancel()
                try:
                    await asyncio.wait_for(asyncio.gather(self._task, return_exceptions=True), timeout=1.0)
                except TimeoutError:
                    LOGGER.warning("Phone Gateway shutdown exceeded cancellation deadline")
        self._task = None
        self._server = None
        return self.status()
