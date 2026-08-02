from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


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
    auth: Literal["public", "bearer"] = "bearer"
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
    auth: Literal["public", "bearer"] = "bearer",
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


# This is intentionally a positive allowlist. Admin, local-session, configuration,
# terminal, desktop-live, RPA, and generalized workspace file routes are absent.
PHONE_GATEWAY_ROUTES: tuple[PhoneGatewayRoute, ...] = (
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
    _route("auth.profile", r"/api/client/auth/profile", ("GET",)),
    _route("connection", r"/api/client/connection", ("GET",)),
    _route("projects.read", r"/api/client/projects", ("GET",)),
    _route("conversations", r"/api/client/conversations", ("GET", "POST")),
    _route("conversation", rf"/api/client/conversations/{_SEGMENT}", ("GET", "PATCH", "DELETE")),
    _route("conversation.turns", rf"/api/client/conversations/{_SEGMENT}/turns", ("GET",)),
    _route("conversation.sync", rf"/api/client/conversations/{_SEGMENT}/sync", ("GET",)),
    _route("session.scope", rf"/api/client/sessions/{_SEGMENT}/scope", ("GET",)),
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
    _route("chat.submit", r"/api/client/chat-submit", ("POST",), max_body_bytes=2 * 1024 * 1024),
    _route("chat.queue", rf"/api/client/chat-queue/{_SEGMENT}", ("PATCH", "DELETE")),
    _route("chat.queue.promote", rf"/api/client/chat-queue/{_SEGMENT}/promote", ("POST",)),
    _route(
        "run.command",
        rf"/api/client/runs/{_SEGMENT}/commands/(?:interrupt|retry)",
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
    _route("sources", r"/api/client/sources", ("GET",)),
    _route("message.delete", rf"/api/client/messages/{_SEGMENT}", ("DELETE",)),
    _route("approvals", r"/api/client/approvals", ("GET",)),
    _route("approval.approve", rf"/api/client/approvals/{_SEGMENT}/approve", ("POST",)),
    _route("approval.reject", rf"/api/client/approvals/{_SEGMENT}/reject", ("POST",)),
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
)


@dataclass(frozen=True)
class PhoneGatewayConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = DEFAULT_PHONE_GATEWAY_PORT
    upstream_base_url: str = "http://127.0.0.1:9528"
    allowed_origins: tuple[str, ...] = (
        "capacitor://localhost",
        "http://localhost",
        "https://localhost",
    )
    connect_timeout_seconds: float = 5.0
    response_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.listen_host != "127.0.0.1":
            raise ValueError("phone_gateway_must_listen_on_ipv4_loopback")
        if not 1 <= int(self.listen_port) <= 65535:
            raise ValueError("phone_gateway_invalid_port")
        parsed = urlsplit(self.upstream_base_url.rstrip("/"))
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("phone_gateway_upstream_must_be_loopback_http")
        for origin in self.allowed_origins:
            normalized = str(origin or "").strip().rstrip("/")
            if not normalized or normalized == "*":
                raise ValueError("phone_gateway_origin_must_be_explicit")

    @property
    def upstream_origin(self) -> str:
        return self.upstream_base_url.rstrip("/")


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
    transport: httpx.AsyncBaseTransport | None = None,
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
            if not requested_headers.issubset({"accept", "authorization", "content-type", "last-event-id", "range"}):
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

        token = _bearer_token(request)
        if route.auth == "bearer" and not token:
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
        upstream_headers["x-v8-client-surface"] = "phone"
        upstream_headers["x-v8-request-id"] = request_id
        upstream_headers["accept-encoding"] = "identity"
        query = request.url.query
        upstream_url = f"{gateway_config.upstream_origin}{request.url.path}"
        if query:
            upstream_url = f"{upstream_url}?{query}"

        timeout = httpx.Timeout(
            gateway_config.response_timeout_seconds,
            connect=gateway_config.connect_timeout_seconds,
        )
        client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            upstream_request = client.build_request(
                request.method,
                upstream_url,
                headers=upstream_headers,
                content=bytes(body),
            )
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return await reject(
                request,
                request_id=request_id,
                route_id=route.route_id,
                started_at=started_at,
                status=502,
                code="phone_gateway_upstream_unavailable",
            )

        response_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() in _SAFE_RESPONSE_HEADERS
        }
        response_headers.update(cors)
        response_headers["x-v8-request-id"] = request_id

        async def close_upstream() -> None:
            await upstream_response.aclose()
            await client.aclose()

        if route.stream and upstream_response.is_success:
            async def stream_body() -> AsyncIterator[bytes]:
                try:
                    async for chunk in upstream_response.aiter_raw():
                        yield chunk
                finally:
                    await close_upstream()
                    emit(
                        _compact_audit(
                            request_id=request_id,
                            route_id=route.route_id,
                            method=request.method,
                            outcome="forwarded",
                            status=upstream_response.status_code,
                            started_at=started_at,
                        )
                    )

            return StreamingResponse(
                stream_body(),
                status_code=upstream_response.status_code,
                headers=response_headers,
                media_type=upstream_response.headers.get("content-type"),
            )

        response_body = await upstream_response.aread()
        await close_upstream()
        emit(
            _compact_audit(
                request_id=request_id,
                route_id=route.route_id,
                method=request.method,
                outcome="forwarded",
                status=upstream_response.status_code,
                started_at=started_at,
            )
        )
        return Response(
            content=response_body,
            status_code=upstream_response.status_code,
            headers=response_headers,
        )

    return app


class PhoneGatewayServer:
    """In-process loopback server for the future Cloudflare Tunnel controller."""

    def __init__(
        self,
        config: PhoneGatewayConfig | None = None,
        *,
        app: FastAPI | None = None,
    ) -> None:
        self.config = config or PhoneGatewayConfig()
        self.app = app or create_phone_gateway_app(self.config)
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return bool(self._server and self._server.started and self._task and not self._task.done())

    def status(self) -> dict[str, Any]:
        return {
            "state": "running" if self.running else "stopped",
            "listenOrigin": f"http://{self.config.listen_host}:{self.config.listen_port}",
            "upstreamKind": "admin_bff",
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
        )
        self._server = uvicorn.Server(uvicorn_config)
        self._server.install_signal_handlers = lambda: None
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while not self._server.started:
            if self._task.done():
                await self._task
                raise RuntimeError("phone_gateway_start_failed")
            if time.monotonic() >= deadline:
                self._server.should_exit = True
                await asyncio.gather(self._task, return_exceptions=True)
                raise TimeoutError("phone_gateway_start_timeout")
            await asyncio.sleep(0.01)
        return self.status()

    async def stop(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=max(0.1, timeout_seconds))
            except TimeoutError:
                if self._server is not None:
                    self._server.force_exit = True
                await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._server = None
        return self.status()
