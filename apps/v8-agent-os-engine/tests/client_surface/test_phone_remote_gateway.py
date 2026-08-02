from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from core.remote_link.phone_gateway import (
    GatewayRatePolicy,
    PhoneGatewayConfig,
    PhoneGatewayServer,
    _WindowRateLimiter,
    create_phone_gateway_app,
)


def _run(coro):
    return asyncio.run(coro)


def _handler_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ok": True,
            "path": request.url.path,
            "authorization": request.headers.get("authorization"),
        },
        headers={"x-v8-engine-now": "2026-08-02T12:00:00Z"},
    )


async def _request(
    app,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | AsyncIterator[bytes] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.local",
    ) as client:
        return await client.request(method, path, headers=headers, content=content)


def test_gateway_configuration_is_loopback_only() -> None:
    config = PhoneGatewayConfig()
    assert config.listen_host == "127.0.0.1"
    assert config.upstream_origin == "http://127.0.0.1:9528"

    with pytest.raises(ValueError, match="phone_gateway_must_listen_on_ipv4_loopback"):
        PhoneGatewayConfig(listen_host="0.0.0.0")
    with pytest.raises(ValueError, match="phone_gateway_upstream_must_be_loopback_http"):
        PhoneGatewayConfig(upstream_base_url="https://admin.example.com")
    with pytest.raises(ValueError, match="phone_gateway_upstream_must_be_loopback_http"):
        PhoneGatewayConfig(upstream_base_url="http://127.0.0.1:9528/api/client")
    with pytest.raises(ValueError, match="phone_gateway_origin_must_be_explicit"):
        PhoneGatewayConfig(allowed_origins=("*",))


def test_public_pairing_and_refresh_routes_forward_without_bearer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    instance = _run(_request(app, "GET", "/api/client/instance"))
    pairing = _run(
        _request(
            app,
            "POST",
            "/api/client/pairing/consume",
            headers={"content-type": "application/json"},
            content=b'{"code":"PAIR-1","instanceId":"instance-1"}',
        )
    )
    refresh = _run(
        _request(
            app,
            "POST",
            "/api/client/auth/refresh",
            headers={"content-type": "application/json"},
            content=b'{"refreshToken":"refresh-secret"}',
        )
    )

    assert [instance.status_code, pairing.status_code, refresh.status_code] == [200, 200, 200]
    assert [request.url.path for request in seen] == [
        "/api/client/instance",
        "/api/client/pairing/consume",
        "/api/client/auth/refresh",
    ]


def test_protected_route_requires_bearer_and_upstream_remains_authoritative() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") == "Bearer expired-token":
            return httpx.Response(401, json={"error": "Invalid access token"})
        return _handler_response(request)

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    missing = _run(_request(app, "GET", "/api/client/conversations"))
    expired = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations",
            headers={"authorization": "Bearer expired-token"},
        )
    )
    accepted = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations",
            headers={"authorization": "Bearer valid-token"},
        )
    )

    assert missing.status_code == 401
    assert missing.json()["error"] == "phone_gateway_bearer_required"
    assert expired.status_code == 401
    assert accepted.json()["authorization"] == "Bearer valid-token"


@pytest.mark.parametrize(
    ("path", "status", "error"),
    [
        ("/api/client/conversations", 403, "owner_mismatch"),
        ("/api/client/pairing/consume", 409, "instance_mismatch"),
        ("/api/client/pairing/consume", 410, "pairing_ticket_consumed"),
    ],
)
def test_gateway_preserves_upstream_owner_instance_and_replay_rejections(
    path: str,
    status: int,
    error: str,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": error})

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    headers = {"content-type": "application/json"}
    if path == "/api/client/conversations":
        headers["authorization"] = "Bearer wrong-owner-token"
    response = _run(_request(app, "GET" if status == 403 else "POST", path, headers=headers, content=b"{}"))

    assert response.status_code == status
    assert response.json() == {"error": error}


def test_gateway_strips_spoofable_headers_and_preserves_phone_headers() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    response = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations?limit=10",
            headers={
                "authorization": "Bearer valid-token",
                "cookie": "admin-session=secret",
                "x-forwarded-for": "203.0.113.8",
                "x-v8-agent-os-secret": "internal-secret",
                "x-v8-client-surface": "forged",
                "accept": "application/json",
                "accept-encoding": "gzip",
            },
        )
    )

    assert response.status_code == 200
    assert seen["authorization"] == "Bearer valid-token"
    assert seen["x-v8-client-surface"] == "phone"
    assert seen["accept-encoding"] == "identity"
    assert "cookie" not in seen
    assert "x-forwarded-for" not in seen
    assert "x-v8-agent-os-secret" not in seen


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/client/auth/local-session"),
        ("GET", "/api/admin/config"),
        ("GET", "/v1/config/system-base"),
        ("GET", "/api/client/workspace/files/private.txt"),
        ("GET", "/api/client/workspace/resource?path=private.txt"),
        ("GET", "/api/client/sessions/session-1/workbench/files/read?path=private.txt"),
        ("GET", "/api/client/sessions/session-1/processes"),
        ("PUT", "/api/client/sessions/session-1/scope"),
        ("POST", "/api/client/sessions/session-1/scope/re-resolve"),
        ("PATCH", "/api/client/auth/profile"),
        ("POST", "/api/client/user-avatar-upload"),
        ("POST", "/api/client/user-background-upload"),
        ("GET", "/api/client/terminal/profiles"),
        ("GET", "/api/client/desktop-live/status"),
        ("GET", "/api/client/rpa/availability"),
        ("POST", "/api/client/projects"),
        ("POST", "/api/client/conversations/session-1/turns"),
    ],
)
def test_privileged_and_unlisted_routes_are_rejected(method: str, path: str) -> None:
    upstream_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200)

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    response = _run(_request(app, method, path, headers={"authorization": "Bearer valid-token"}))

    assert response.status_code == 404
    assert response.json()["error"] == "phone_gateway_route_not_allowed"
    assert upstream_calls == 0


@pytest.mark.parametrize(
    "path",
    [
        "/api/client/conversations/%2e%2e/auth/local-session",
        "/api/client/conversations/session%2fescape",
        "/api/client/conversations/session%5cescape",
    ],
)
def test_encoded_path_bypasses_are_rejected(path: str) -> None:
    app = create_phone_gateway_app(transport=httpx.MockTransport(_handler_response))
    response = _run(_request(app, "GET", path, headers={"authorization": "Bearer valid-token"}))
    assert response.status_code == 404
    assert response.json()["error"] == "phone_gateway_route_not_allowed"


def test_request_body_limits_cover_declared_and_streamed_payloads() -> None:
    app = create_phone_gateway_app(transport=httpx.MockTransport(_handler_response))
    declared = _run(
        _request(
            app,
            "POST",
            "/api/client/chat-submit",
            headers={
                "authorization": "Bearer valid-token",
                "content-length": str(2 * 1024 * 1024 + 1),
            },
            content=b"{}",
        )
    )

    async def oversized() -> AsyncIterator[bytes]:
        yield b"a" * (1024 * 1024)
        yield b"b" * (1024 * 1024 + 1)

    streamed = _run(
        _request(
            app,
            "POST",
            "/api/client/chat-submit",
            headers={"authorization": "Bearer valid-token"},
            content=oversized(),
        )
    )

    assert declared.status_code == 413
    assert streamed.status_code == 413
    assert declared.json()["error"] == "phone_gateway_body_too_large"
    assert streamed.json()["error"] == "phone_gateway_body_too_large"


def test_origin_policy_and_preflight_are_explicit() -> None:
    app = create_phone_gateway_app(
        PhoneGatewayConfig(allowed_origins=("capacitor://localhost", "https://phone.example.com")),
        transport=httpx.MockTransport(_handler_response),
    )
    denied = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations",
            headers={"authorization": "Bearer valid-token", "origin": "https://evil.example"},
        )
    )
    allowed = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations",
            headers={"authorization": "Bearer valid-token", "origin": "https://phone.example.com"},
        )
    )
    preflight = _run(
        _request(
            app,
            "OPTIONS",
            "/api/client/chat-submit",
            headers={
                "origin": "https://phone.example.com",
                "access-control-request-method": "POST",
                "access-control-request-headers": "authorization, content-type",
            },
        )
    )

    assert denied.status_code == 403
    assert allowed.headers["access-control-allow-origin"] == "https://phone.example.com"
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-methods"] == "POST"


def test_rate_limit_is_scoped_to_route_and_token_without_logging_token() -> None:
    clock_value = 100.0
    limiter = _WindowRateLimiter(clock=lambda: clock_value)
    events: list[dict[str, object]] = []
    app = create_phone_gateway_app(
        transport=httpx.MockTransport(_handler_response),
        audit_sink=events.append,
        rate_limiter=limiter,
    )
    # Narrow the policy for this isolated limiter without mutating the global route table.
    policy = GatewayRatePolicy(requests=1)

    async def reserve() -> tuple[bool, bool]:
        return (
            await limiter.allow("token:redacted:conversations", policy),
            await limiter.allow("token:redacted:conversations", policy),
        )

    assert _run(reserve()) == (True, False)

    response = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations",
            headers={"authorization": "Bearer top-secret-token"},
        )
    )
    assert response.status_code == 200
    serialized = json.dumps(events, ensure_ascii=False)
    assert "top-secret-token" not in serialized
    assert "authorization" not in serialized.lower()


def test_pairing_rate_limit_rejects_the_ninth_request_without_upstream_call() -> None:
    upstream_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal upstream_calls
        upstream_calls += 1
        return httpx.Response(200, json={"ok": True})

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))

    async def exercise() -> list[int]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.local",
        ) as client:
            responses = [
                await client.post(
                    "/api/client/pairing/consume",
                    json={"code": f"PAIR-{index}", "instanceId": "instance-1"},
                )
                for index in range(9)
            ]
        return [response.status_code for response in responses]

    statuses = _run(exercise())
    assert statuses[:8] == [200] * 8
    assert statuses[8] == 429
    assert upstream_calls == 8


def test_sse_forwards_last_event_id_and_streams_without_buffering_contract_loss() -> None:
    seen: dict[str, str] = {}

    class _EventStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"event: ready\ndata: {}\n\n"
            yield b"event: activity\ndata: {\"seq\":1}\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "cache-control": "no-cache, no-transform"},
            stream=_EventStream(),
        )

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    response = _run(
        _request(
            app,
            "GET",
            "/api/client/realtime/session-activity/stream",
            headers={
                "authorization": "Bearer valid-token",
                "accept": "text/event-stream",
                "last-event-id": "activity-41",
            },
        )
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert seen["last-event-id"] == "activity-41"
    assert "event: activity" in response.text


def test_artifact_content_preserves_range_and_streaming_headers() -> None:
    seen: dict[str, str] = {}

    class _ArtifactStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"artifact-"
            yield b"bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(
            206,
            headers={
                "content-type": "video/mp4",
                "content-range": "bytes 0-13/1024",
                "accept-ranges": "bytes",
            },
            stream=_ArtifactStream(),
        )

    app = create_phone_gateway_app(transport=httpx.MockTransport(handler))
    response = _run(
        _request(
            app,
            "GET",
            "/api/client/artifacts/artifact-1/content",
            headers={"authorization": "Bearer valid-token", "range": "bytes=0-13"},
        )
    )

    assert response.status_code == 206
    assert response.content == b"artifact-bytes"
    assert seen["range"] == "bytes=0-13"
    assert response.headers["content-range"] == "bytes 0-13/1024"
    assert response.headers["accept-ranges"] == "bytes"


def test_upstream_failure_is_compact_and_audited_without_trace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret upstream detail", request=request)

    events: list[dict[str, object]] = []
    app = create_phone_gateway_app(
        transport=httpx.MockTransport(handler),
        audit_sink=events.append,
    )
    response = _run(
        _request(
            app,
            "GET",
            "/api/client/conversations",
            headers={"authorization": "Bearer valid-token"},
        )
    )

    assert response.status_code == 502
    assert response.json()["error"] == "phone_gateway_upstream_unavailable"
    assert "secret upstream detail" not in response.text
    assert events[-1]["outcome"] == "phone_gateway_upstream_unavailable"


def test_server_lifecycle_binds_only_configured_loopback() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])

    async def exercise() -> None:
        server = PhoneGatewayServer(PhoneGatewayConfig(listen_port=port))
        started = await server.start()
        assert started["state"] == "running"
        assert started["listenOrigin"] == f"http://127.0.0.1:{port}"
        stopped = await server.stop()
        assert stopped["state"] == "stopped"

    _run(exercise())
