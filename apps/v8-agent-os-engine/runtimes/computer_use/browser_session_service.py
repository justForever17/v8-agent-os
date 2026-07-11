from __future__ import annotations

import asyncio
import base64
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from core.workbench_events import emit_workbench_document_event


CONTROL_LEASE_TTL_SECONDS = 15.0
CONTROL_HEARTBEAT_SECONDS = 5.0
WS_TICKET_TTL_SECONDS = 30.0
MAX_TEXT_INPUT_CHARS = 20_000
MAX_URL_CHARS = 8_192


class BrowserSessionError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(slots=True)
class _BrowserSession:
    browser_session_id: str
    session_id: str
    provider: Any
    target_id: str
    target_port: int
    browser_kind: str
    managed_headless: bool
    external_window: bool
    status: str = "ready"
    current_target_id: str = ""
    page_ids: dict[str, str] = field(default_factory=dict)
    controller_client_id: str | None = None
    lease_expires_at: float = 0.0
    agent_reobserve_required: bool = False
    stream_mode: str = "idle"
    viewer_count: int = 0
    refresh_failures: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    unavailable_reason: str = ""


@dataclass(slots=True)
class _WsTicket:
    ticket: str
    browser_session_id: str
    client_id: str
    expires_at: float


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _finite_number(value: Any, *, minimum: float, maximum: float, name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise BrowserSessionError("invalid_input", f"{name} must be a number") from exc
    if not math.isfinite(normalized) or normalized < minimum or normalized > maximum:
        raise BrowserSessionError("invalid_input", f"{name} is outside the supported range")
    return normalized


class BrowserSessionService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, _BrowserSession] = {}
        self._target_sessions: dict[str, set[str]] = {}
        self._tickets: dict[str, _WsTicket] = {}

    def reset_for_tests(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._target_sessions.clear()
            self._tickets.clear()

    def _session(self, browser_session_id: str, *, allow_unavailable: bool = False) -> _BrowserSession:
        normalized = str(browser_session_id or "").strip()
        with self._lock:
            item = self._sessions.get(normalized)
        if item is None:
            raise BrowserSessionError("browser_session_not_found", "Browser session was not found", status_code=404)
        if not allow_unavailable and item.status == "unavailable":
            raise BrowserSessionError(
                "browser_session_unavailable",
                item.unavailable_reason or "Browser session is unavailable",
                status_code=410,
            )
        return item

    @staticmethod
    def _document(item: _BrowserSession) -> dict[str, Any]:
        return {
            "kind": "browser",
            "documentId": f"browser:{item.browser_session_id}",
            "title": "浏览器",
            "renderer": "browser",
            "lifecycle": "runtime",
            "status": "unavailable" if item.status == "unavailable" else "available",
            "capabilities": ["interact", "navigate", "control", "focus"],
            "subjectRef": {
                "browserSessionId": item.browser_session_id,
                "sessionId": item.session_id,
            },
            **(
                {"unavailableReason": item.unavailable_reason}
                if item.unavailable_reason
                else {}
            ),
        }

    def create_session(
        self,
        *,
        session_id: str,
        provider: Any,
        url: str = "about:blank",
        browser_kind: str | None = None,
        focus_requested: bool = True,
        user_initiated: bool = True,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise BrowserSessionError("invalid_session", "sessionId is required")
        opened = _record(provider.open_workbench_browser(browser_kind=browser_kind, url=url))
        target_id = str(opened.get("targetId") or "").strip()
        target_port = int(opened.get("targetPort") or 0)
        if not target_id or target_port <= 0:
            raise BrowserSessionError("browser_target_unavailable", "Workbench browser did not return a usable page")
        browser_session_id = f"browser_{secrets.token_urlsafe(18)}"
        item = _BrowserSession(
            browser_session_id=browser_session_id,
            session_id=normalized_session_id,
            provider=provider,
            target_id=target_id,
            current_target_id=target_id,
            target_port=target_port,
            browser_kind=str(opened.get("browserKind") or browser_kind or "chromium"),
            managed_headless=bool(opened.get("managedHeadless")),
            external_window=bool(opened.get("externalWindow")),
        )
        with self._lock:
            self._sessions[browser_session_id] = item
            self._target_sessions.setdefault(target_id, set()).add(browser_session_id)
        try:
            self._refresh_pages(item)
        except Exception:
            pass
        emit_workbench_document_event(
            "workbench.document.opened",
            session_id=normalized_session_id,
            document=self._document(item),
            runtime_id="computer_use",
            source_component="computer_use_browser_sessions",
            focus_requested=focus_requested,
            user_initiated=user_initiated,
        )
        return self.public_status(browser_session_id, refresh=False)

    def register_existing_target(
        self,
        *,
        session_id: str,
        provider: Any,
        opened: dict[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        target_id = str(opened.get("targetId") or "").strip()
        target_port = int(opened.get("targetPort") or 0)
        if not normalized_session_id or not target_id or target_port <= 0:
            raise BrowserSessionError(
                "browser_target_unavailable",
                "An existing browser target requires sessionId, targetId and targetPort",
            )
        with self._lock:
            for browser_session_id in list(self._target_sessions.get(target_id) or []):
                existing = self._sessions.get(browser_session_id)
                if existing and existing.session_id == normalized_session_id and existing.status != "unavailable":
                    return self.public_status(existing.browser_session_id, refresh=False)

        browser_session_id = f"browser_{secrets.token_urlsafe(18)}"
        item = _BrowserSession(
            browser_session_id=browser_session_id,
            session_id=normalized_session_id,
            provider=provider,
            target_id=target_id,
            current_target_id=target_id,
            target_port=target_port,
            browser_kind=str(opened.get("browserKind") or opened.get("family") or "chromium"),
            managed_headless=bool(opened.get("managedHeadless")),
            external_window=bool(opened.get("externalWindow", not bool(opened.get("managedHeadless")))),
        )
        with self._lock:
            self._sessions[browser_session_id] = item
            self._target_sessions.setdefault(target_id, set()).add(browser_session_id)
        try:
            self._refresh_pages(item)
        except Exception:
            pass
        emit_workbench_document_event(
            "workbench.document.opened",
            session_id=normalized_session_id,
            run_id=str(run_id or "").strip() or None,
            document=self._document(item),
            runtime_id="computer_use",
            source_component="computer_use_browser_sessions",
            focus_requested=False,
            user_initiated=False,
        )
        return self.public_status(browser_session_id, refresh=False)

    def _opaque_page_id(self, item: _BrowserSession, target_id: str) -> str:
        page_id = item.page_ids.get(target_id)
        if page_id:
            return page_id
        page_id = f"page_{secrets.token_urlsafe(10)}"
        item.page_ids[target_id] = page_id
        return page_id

    def _refresh_pages(self, item: _BrowserSession) -> list[dict[str, Any]]:
        response = item.provider.workbench_request_json(
            "GET",
            "/targets",
            target_port=item.target_port,
        )
        targets = response if isinstance(response, list) else _record(response).get("targets")
        targets = targets if isinstance(targets, list) else []
        pages: list[dict[str, Any]] = []
        target_ids: set[str] = set()
        with self._lock:
            for raw in targets:
                target = _record(raw)
                target_id = str(target.get("targetId") or target.get("id") or "").strip()
                if not target_id:
                    continue
                target_ids.add(target_id)
                self._target_sessions.setdefault(target_id, set()).add(item.browser_session_id)
                pages.append(
                    {
                        "pageId": self._opaque_page_id(item, target_id),
                        "title": str(target.get("title") or "新标签页")[:300],
                        "url": str(target.get("url") or "about:blank")[:MAX_URL_CHARS],
                        "active": target_id == item.current_target_id,
                    }
                )
            for stale_target in list(item.page_ids):
                if stale_target not in target_ids:
                    item.page_ids.pop(stale_target, None)
                    owners = self._target_sessions.get(stale_target)
                    if owners:
                        owners.discard(item.browser_session_id)
                        if not owners:
                            self._target_sessions.pop(stale_target, None)
            if item.current_target_id not in target_ids and target_ids:
                item.current_target_id = next(iter(target_ids))
            item.refresh_failures = 0
            if item.status == "degraded":
                item.status = "ready"
            item.updated_at = time.time()
        return pages

    def _expire_lease(self, item: _BrowserSession) -> None:
        if item.controller_client_id and item.lease_expires_at <= time.monotonic():
            item.controller_client_id = None
            item.lease_expires_at = 0.0
            item.agent_reobserve_required = True

    def public_status(self, browser_session_id: str, *, refresh: bool = True) -> dict[str, Any]:
        item = self._session(browser_session_id, allow_unavailable=True)
        pages: list[dict[str, Any]] = []
        if refresh and item.status != "unavailable":
            try:
                pages = self._refresh_pages(item)
            except Exception:
                with self._lock:
                    item.refresh_failures += 1
                    item.status = "degraded"
                    item.updated_at = time.time()
                    exhausted = item.refresh_failures >= 3
                if exhausted:
                    self.mark_unavailable(browser_session_id, "Browser transport is unavailable")
                    item = self._session(browser_session_id, allow_unavailable=True)
        if not pages:
            with self._lock:
                pages = [
                    {
                        "pageId": page_id,
                        "title": "浏览器页面",
                        "url": "",
                        "active": target_id == item.current_target_id,
                    }
                    for target_id, page_id in item.page_ids.items()
                ]
        with self._lock:
            self._expire_lease(item)
            current_page_id = item.page_ids.get(item.current_target_id)
            controlled = bool(item.controller_client_id)
            lease_expires_at = item.lease_expires_at
            return {
                "browserSessionId": item.browser_session_id,
                "sessionId": item.session_id,
                "status": item.status,
                "createdAt": _iso(item.created_at),
                "updatedAt": _iso(item.updated_at),
                "browserKind": item.browser_kind,
                "managedHeadless": item.managed_headless,
                "externalWindow": item.external_window,
                "currentPageId": current_page_id,
                "pages": pages,
                "stream": {"mode": item.stream_mode},
                "control": {
                    "state": "user" if controlled else "agent",
                    "leaseTtlSeconds": CONTROL_LEASE_TTL_SECONDS,
                    "heartbeatSeconds": CONTROL_HEARTBEAT_SECONDS,
                    "leaseExpiresAt": _iso(time.time() + max(0.0, lease_expires_at - time.monotonic())) if controlled else None,
                    "agentReobserveRequired": item.agent_reobserve_required,
                },
                "unavailableReason": item.unavailable_reason or None,
                "limitations": [
                    "file_picker",
                    "download_manager",
                    "passkey",
                    "hardware_key",
                    "media_permissions",
                    "extensions",
                    "devtools",
                    "drm",
                ],
            }

    def issue_ws_ticket(self, browser_session_id: str) -> dict[str, Any]:
        self._session(browser_session_id)
        now = time.time()
        ticket_value = secrets.token_urlsafe(32)
        ticket = _WsTicket(
            ticket=ticket_value,
            browser_session_id=browser_session_id,
            client_id=f"client_{secrets.token_urlsafe(12)}",
            expires_at=now + WS_TICKET_TTL_SECONDS,
        )
        with self._lock:
            self._tickets = {
                key: value
                for key, value in self._tickets.items()
                if value.expires_at > now
            }
            self._tickets[ticket_value] = ticket
        return {
            "ticket": ticket_value,
            "clientId": ticket.client_id,
            "expiresAt": _iso(ticket.expires_at),
        }

    def consume_ws_ticket(self, browser_session_id: str, ticket_value: str) -> _WsTicket:
        normalized = str(ticket_value or "").strip()
        with self._lock:
            ticket = self._tickets.pop(normalized, None)
        if ticket is None or ticket.expires_at <= time.time() or ticket.browser_session_id != browser_session_id:
            raise BrowserSessionError("invalid_ws_ticket", "Browser WebSocket ticket is invalid or expired", status_code=403)
        self._session(browser_session_id)
        return ticket

    def _target_for_page(self, item: _BrowserSession, page_id: Any) -> str:
        normalized = str(page_id or "").strip()
        with self._lock:
            if not normalized:
                return item.current_target_id
            for target_id, opaque_id in item.page_ids.items():
                if opaque_id == normalized:
                    return target_id
        raise BrowserSessionError("page_not_found", "Browser page was not found", status_code=404)

    def take_control(self, browser_session_id: str, client_id: str) -> dict[str, Any]:
        item = self._session(browser_session_id)
        normalized_client_id = str(client_id or "").strip()
        if not normalized_client_id:
            raise BrowserSessionError("invalid_client", "clientId is required")
        with self._lock:
            self._expire_lease(item)
            if item.controller_client_id and item.controller_client_id != normalized_client_id:
                raise BrowserSessionError("control_busy", "Another Workbench client controls this browser", status_code=409)
            item.controller_client_id = normalized_client_id
            item.lease_expires_at = time.monotonic() + CONTROL_LEASE_TTL_SECONDS
            item.updated_at = time.time()
        return self.public_status(browser_session_id, refresh=False)

    def heartbeat(self, browser_session_id: str, client_id: str) -> dict[str, Any]:
        item = self._session(browser_session_id)
        with self._lock:
            self._expire_lease(item)
            if item.controller_client_id != str(client_id or "").strip():
                raise BrowserSessionError("control_required", "User control lease is not active", status_code=409)
            item.lease_expires_at = time.monotonic() + CONTROL_LEASE_TTL_SECONDS
        return {"ok": True, "leaseTtlSeconds": CONTROL_LEASE_TTL_SECONDS}

    def release_control(self, browser_session_id: str, client_id: str) -> dict[str, Any]:
        item = self._session(browser_session_id, allow_unavailable=True)
        with self._lock:
            if item.controller_client_id == str(client_id or "").strip():
                item.controller_client_id = None
                item.lease_expires_at = 0.0
                item.agent_reobserve_required = True
                item.updated_at = time.time()
        return {"ok": True, "agentReobserveRequired": item.agent_reobserve_required}

    def assert_agent_control_available_for_target(self, target_id: str) -> None:
        normalized = str(target_id or "").strip()
        with self._lock:
            session_ids = list(self._target_sessions.get(normalized) or [])
            for browser_session_id in session_ids:
                item = self._sessions.get(browser_session_id)
                if item is None or item.status == "unavailable":
                    continue
                self._expire_lease(item)
                if item.controller_client_id:
                    raise BrowserSessionError(
                        "browser_user_control_active",
                        "Workbench user control is active; Agent browser input is blocked",
                        status_code=409,
                    )
                if item.agent_reobserve_required:
                    raise BrowserSessionError(
                        "browser_reobserve_required",
                        "Agent must observe the page again after user control",
                        status_code=409,
                    )

    def note_agent_observation(self, target_id: str) -> None:
        normalized = str(target_id or "").strip()
        with self._lock:
            for browser_session_id in list(self._target_sessions.get(normalized) or []):
                item = self._sessions.get(browser_session_id)
                if item is None:
                    continue
                self._expire_lease(item)
                if not item.controller_client_id:
                    item.agent_reobserve_required = False

    @staticmethod
    def _normalize_url(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise BrowserSessionError("invalid_url", "url is required")
        if len(raw) > MAX_URL_CHARS:
            raise BrowserSessionError("invalid_url", "url is too long")
        if raw == "about:blank":
            return raw
        initial = urlparse(raw)
        if initial.scheme and initial.scheme not in {"http", "https"}:
            raise BrowserSessionError("invalid_url", "Only http/https URLs are supported")
        if not initial.scheme:
            raw = f"https://{raw}"
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BrowserSessionError("invalid_url", "Only http/https URLs are supported")
        return raw

    def _require_user_control(self, item: _BrowserSession, client_id: str) -> None:
        with self._lock:
            self._expire_lease(item)
            if item.controller_client_id != str(client_id or "").strip():
                raise BrowserSessionError("control_required", "Take control before interacting with the browser", status_code=409)

    def handle_command(
        self,
        browser_session_id: str,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        item = self._session(browser_session_id)
        action = str(message.get("action") or message.get("type") or "").strip()
        request_id = str(message.get("requestId") or "").strip() or None
        try:
            if action in {"status", "list_pages"}:
                result = self.public_status(browser_session_id)
            elif action in {"take_control", "takeover"}:
                result = self.take_control(browser_session_id, client_id)
            elif action == "heartbeat":
                result = self.heartbeat(browser_session_id, client_id)
            elif action in {"release_control", "release"}:
                result = self.release_control(browser_session_id, client_id)
            else:
                self._require_user_control(item, client_id)
                target_id = self._target_for_page(item, message.get("pageId"))
                if action == "new_tab":
                    created = item.provider.workbench_request_json(
                        "GET",
                        "/new",
                        target_port=item.target_port,
                        params={"url": self._normalize_url(message.get("url") or "about:blank")},
                    )
                    target_id = str(_record(created).get("targetId") or "").strip()
                    if target_id:
                        with self._lock:
                            item.current_target_id = target_id
                            self._target_sessions.setdefault(target_id, set()).add(item.browser_session_id)
                    result = self.public_status(browser_session_id)
                elif action == "close_page":
                    item.provider.workbench_request_json(
                        "POST",
                        "/close",
                        target_port=item.target_port,
                        params={"target": target_id},
                    )
                    result = self.public_status(browser_session_id)
                else:
                    helper_action = action
                    body: dict[str, Any] = {"action": helper_action}
                    if action == "navigate":
                        body["url"] = self._normalize_url(message.get("url"))
                    elif action in {"mouseMoved", "mousePressed", "mouseReleased", "mouseWheel"}:
                        body["x"] = _finite_number(message.get("x", 0), minimum=0, maximum=10_000, name="x")
                        body["y"] = _finite_number(message.get("y", 0), minimum=0, maximum=10_000, name="y")
                        if action == "mouseWheel":
                            body["deltaX"] = _finite_number(message.get("deltaX", 0), minimum=-10_000, maximum=10_000, name="deltaX")
                            body["deltaY"] = _finite_number(message.get("deltaY", 0), minimum=-10_000, maximum=10_000, name="deltaY")
                        else:
                            body["button"] = str(message.get("button") or "none")[:16]
                            body["buttons"] = int(_finite_number(message.get("buttons", 0), minimum=0, maximum=31, name="buttons"))
                            body["clickCount"] = int(_finite_number(message.get("clickCount", 0), minimum=0, maximum=3, name="clickCount"))
                    elif action in {"keyDown", "rawKeyDown", "keyUp", "char"}:
                        body.update(
                            {
                                "key": str(message.get("key") or "")[:128],
                                "code": str(message.get("code") or "")[:128],
                                "text": str(message.get("text") or "")[:128],
                                "unmodifiedText": str(message.get("unmodifiedText") or message.get("text") or "")[:128],
                                "autoRepeat": bool(message.get("autoRepeat")),
                            }
                        )
                    elif action == "insertText":
                        text = str(message.get("text") or "")
                        if len(text) > MAX_TEXT_INPUT_CHARS:
                            raise BrowserSessionError("invalid_input", "text input is too long")
                        body["text"] = text
                    elif action not in {"back", "forward", "reload", "activate"}:
                        raise BrowserSessionError("unsupported_command", f"Unsupported browser command: {action}")
                    modifiers = message.get("modifiers")
                    if isinstance(modifiers, list):
                        allowed = {"alt", "control", "ctrl", "meta", "command", "shift"}
                        body["modifiers"] = [str(value).lower() for value in modifiers if str(value).lower() in allowed]
                    item.provider.workbench_request_json(
                        "POST",
                        "/dispatch",
                        target_port=item.target_port,
                        params={"target": target_id},
                        body=body,
                    )
                    with self._lock:
                        item.current_target_id = target_id
                    result = self.public_status(browser_session_id)
            return {"type": "command_result", "requestId": request_id, "ok": True, "result": result}
        except BrowserSessionError as exc:
            return {
                "type": "command_result",
                "requestId": request_id,
                "ok": False,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception:
            return {
                "type": "command_result",
                "requestId": request_id,
                "ok": False,
                "error": {
                    "code": "browser_command_failed",
                    "message": "The browser command could not be completed",
                },
            }

    def mark_unavailable(self, browser_session_id: str, reason: str) -> dict[str, Any]:
        item = self._session(browser_session_id, allow_unavailable=True)
        normalized_reason = str(reason or "Browser session is unavailable").strip()[:500]
        with self._lock:
            if item.status == "unavailable" and item.unavailable_reason == normalized_reason:
                return self.public_status(browser_session_id, refresh=False)
            item.status = "unavailable"
            item.unavailable_reason = normalized_reason
            item.controller_client_id = None
            item.lease_expires_at = 0.0
            item.updated_at = time.time()
        emit_workbench_document_event(
            "workbench.document.unavailable",
            session_id=item.session_id,
            document=self._document(item),
            runtime_id="computer_use",
            source_component="computer_use_browser_sessions",
        )
        return self.public_status(browser_session_id, refresh=False)

    def delete_session(self, browser_session_id: str) -> dict[str, Any]:
        item = self._session(browser_session_id, allow_unavailable=True)
        # Deleting the presentation session never silently closes the underlying browser page.
        return self.mark_unavailable(browser_session_id, "Browser presentation session was closed")

    async def _send_json(self, websocket: Any, send_lock: asyncio.Lock, payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def _send_frame(self, websocket: Any, send_lock: asyncio.Lock, frame: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(frame)

    async def _frame_loop(self, item: _BrowserSession, websocket: Any, send_lock: asyncio.Lock) -> None:
        last_seq = 0
        fallback = False
        stream_target_id = item.current_target_id
        try:
            await asyncio.to_thread(
                item.provider.workbench_request_json,
                "POST",
                "/screencast/start",
                target_port=item.target_port,
                params={"target": stream_target_id},
                body={"quality": 70, "maxWidth": 1920, "maxHeight": 1200},
            )
            with self._lock:
                item.stream_mode = "screencast"
            await self._send_json(websocket, send_lock, {"type": "stream_status", "mode": "screencast", "maxFps": 15})
        except Exception:
            fallback = True
            with self._lock:
                item.stream_mode = "screenshot_fallback"
            await self._send_json(websocket, send_lock, {"type": "stream_status", "mode": "screenshot_fallback", "maxFps": 2})

        last_status_at = 0.0
        consecutive_frame_errors = 0
        while item.status != "unavailable":
            try:
                if item.current_target_id != stream_target_id:
                    if not fallback:
                        await asyncio.to_thread(
                            item.provider.workbench_request_json,
                            "POST",
                            "/screencast/stop",
                            target_port=item.target_port,
                            params={"target": stream_target_id},
                        )
                    stream_target_id = item.current_target_id
                    last_seq = 0
                    fallback = False
                    try:
                        await asyncio.to_thread(
                            item.provider.workbench_request_json,
                            "POST",
                            "/screencast/start",
                            target_port=item.target_port,
                            params={"target": stream_target_id},
                            body={"quality": 70, "maxWidth": 1920, "maxHeight": 1200},
                        )
                        with self._lock:
                            item.stream_mode = "screencast"
                        await self._send_json(websocket, send_lock, {"type": "stream_status", "mode": "screencast", "maxFps": 15})
                    except Exception:
                        fallback = True
                        with self._lock:
                            item.stream_mode = "screenshot_fallback"
                        await self._send_json(websocket, send_lock, {"type": "stream_status", "mode": "screenshot_fallback", "maxFps": 2})
                if fallback:
                    response = await asyncio.to_thread(
                        item.provider.workbench_request_json,
                        "GET",
                        "/screenshot",
                        target_port=item.target_port,
                        params={"target": stream_target_id},
                    )
                    frame_data = str(_record(response).get("data") or "")
                    if frame_data:
                        await self._send_frame(websocket, send_lock, base64.b64decode(frame_data))
                    consecutive_frame_errors = 0
                    await asyncio.sleep(0.5)
                else:
                    response = await asyncio.to_thread(
                        item.provider.workbench_request_json,
                        "GET",
                        "/screencast/frame",
                        target_port=item.target_port,
                        params={"target": stream_target_id, "after": last_seq},
                    )
                    frame = _record(_record(response).get("frame"))
                    seq = int(frame.get("seq") or 0)
                    frame_data = str(frame.get("data") or "")
                    if seq > last_seq and frame_data:
                        last_seq = seq
                        await self._send_json(
                            websocket,
                            send_lock,
                            {"type": "frame_meta", "seq": seq, "metadata": _record(frame.get("metadata"))},
                        )
                        await self._send_frame(websocket, send_lock, base64.b64decode(frame_data))
                    consecutive_frame_errors = 0
                    await asyncio.sleep(1 / 15)
                if time.monotonic() - last_status_at >= 1.0:
                    last_status_at = time.monotonic()
                    status = await asyncio.to_thread(self.public_status, item.browser_session_id)
                    await self._send_json(websocket, send_lock, {"type": "status", "status": status})
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_frame_errors += 1
                if consecutive_frame_errors < 3:
                    fallback = True
                    with self._lock:
                        item.stream_mode = "screenshot_fallback"
                    await self._send_json(websocket, send_lock, {"type": "stream_status", "mode": "screenshot_fallback", "maxFps": 2})
                    await asyncio.sleep(0.5)
                    continue
                await asyncio.to_thread(self.mark_unavailable, item.browser_session_id, "Browser transport is unavailable")
                await self._send_json(websocket, send_lock, {"type": "unavailable", "reason": item.unavailable_reason})
                return

    async def serve_websocket(self, browser_session_id: str, client_id: str, websocket: Any) -> None:
        item = self._session(browser_session_id)
        send_lock = asyncio.Lock()
        with self._lock:
            item.viewer_count += 1
        await self._send_json(
            websocket,
            send_lock,
            {"type": "hello", "clientId": client_id, "status": self.public_status(browser_session_id)},
        )

        async def receive_loop() -> None:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send_json(
                        websocket,
                        send_lock,
                        {"type": "command_result", "ok": False, "error": {"code": "invalid_json", "message": "Invalid JSON command"}},
                    )
                    continue
                if not isinstance(message, dict):
                    continue
                response = await asyncio.to_thread(self.handle_command, browser_session_id, client_id, message)
                await self._send_json(websocket, send_lock, response)

        receiver = asyncio.create_task(receive_loop())
        frames = asyncio.create_task(self._frame_loop(item, websocket, send_lock))
        try:
            done, pending = await asyncio.wait({receiver, frames}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        finally:
            self.release_control(browser_session_id, client_id)
            should_stop = False
            with self._lock:
                item.viewer_count = max(0, item.viewer_count - 1)
                should_stop = item.viewer_count == 0
                item.stream_mode = "idle"
            if should_stop:
                with self._lock:
                    target_ids = list(item.page_ids) or [item.current_target_id]
                for target_id in target_ids:
                    try:
                        await asyncio.to_thread(
                            item.provider.workbench_request_json,
                            "POST",
                            "/screencast/stop",
                            target_port=item.target_port,
                            params={"target": target_id},
                        )
                    except Exception:
                        pass


browser_session_service = BrowserSessionService()
