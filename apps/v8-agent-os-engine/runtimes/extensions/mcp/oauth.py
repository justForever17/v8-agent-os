from __future__ import annotations

import asyncio
import json
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

from core.security.credentials import CredentialRefStore, CredentialStoreError, credential_ref_store
from core.storage import storage


OAUTH_CALLBACK_URL = "http://127.0.0.1:9530/v1/api/plugins/oauth/callback"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _allowed_host(host: str, allowed_domains: set[str]) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    return bool(normalized) and any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in allowed_domains
    )


class CredentialOAuthTokenStorage(TokenStorage):
    """Persist OAuth tokens/client registration as one opaque OS credential."""

    def __init__(
        self,
        *,
        server_name: str,
        plugin_id: str,
        component_id: str,
        secret_ref: str | None,
        credential_store: CredentialRefStore | None = None,
    ) -> None:
        self.server_name = server_name
        self.plugin_id = plugin_id
        self.component_id = component_id
        self.secret_ref = str(secret_ref or "").strip()
        self._credential_store = credential_store or credential_ref_store
        self._payload_lock = asyncio.Lock()

    async def _read(self) -> dict[str, Any]:
        if not self.secret_ref:
            return {}
        try:
            raw = await asyncio.to_thread(self._credential_store.resolve, self.secret_ref)
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except (CredentialStoreError, ValueError, json.JSONDecodeError):
            return {}

    async def _write(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.secret_ref = await asyncio.to_thread(
            self._credential_store.put,
            raw,
            reference=self.secret_ref or None,
        )
        config = storage.get_mcp_config()
        servers = dict(config.get("mcpServers") or {})
        server = dict(servers.get(self.server_name) or {})
        server["x-v8-oauth"] = {
            "secretRef": self.secret_ref,
            "pluginId": self.plugin_id,
            "componentId": self.component_id,
            "updatedAt": _now_iso(),
        }
        servers[self.server_name] = server
        config["mcpServers"] = servers
        await asyncio.to_thread(storage.save_mcp_config, config)
        if self.plugin_id:
            try:
                from runtimes.plugin_manager.service import plugin_manager_service

                await asyncio.to_thread(
                    plugin_manager_service.refresh_configuration_status,
                    self.plugin_id,
                )
            except Exception:
                # The OAuth token is already durably stored. Configuration
                # projection will reconcile on the next status/read request.
                pass

    async def get_tokens(self) -> OAuthToken | None:
        payload = await self._read()
        value = payload.get("tokens")
        return OAuthToken.model_validate(value) if isinstance(value, dict) else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        async with self._payload_lock:
            payload = await self._read()
            payload["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
            await self._write(payload)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        payload = await self._read()
        value = payload.get("clientInfo")
        return OAuthClientInformationFull.model_validate(value) if isinstance(value, dict) else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        async with self._payload_lock:
            payload = await self._read()
            payload["clientInfo"] = client_info.model_dump(mode="json", exclude_none=True)
            await self._write(payload)


@dataclass(slots=True)
class _PendingOAuthFlow:
    server_name: str
    state: str
    nonce: str
    future: asyncio.Future[tuple[str, str | None]]
    created_at: str
    completed: bool = False


class McpOAuthCoordinator:
    def __init__(self) -> None:
        self._flows: dict[str, _PendingOAuthFlow] = {}
        self._server_state: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _set_status(self, server_name: str, status: str, **details: Any) -> None:
        with self._lock:
            self._server_state[server_name] = {
                "serverName": server_name,
                "status": status,
                "updatedAt": _now_iso(),
                **details,
            }

    def status(self, server_name: str) -> dict[str, Any]:
        with self._lock:
            return dict(
                self._server_state.get(server_name)
                or {"serverName": server_name, "status": "idle", "updatedAt": _now_iso()}
            )

    def mark_connected(self, server_name: str) -> None:
        self._set_status(server_name, "connected")

    def mark_failed(self, server_name: str, error: str) -> None:
        self._set_status(server_name, "failed", error=str(error or "oauth_failed")[:240])

    async def _register_redirect(self, server_name: str, authorization_url: str, allowed_domains: set[str]) -> None:
        parsed = urlparse(authorization_url)
        if parsed.scheme != "https" or not _allowed_host(parsed.hostname or "", allowed_domains):
            self._set_status(server_name, "failed", error="oauth_authorization_origin_denied")
            raise ValueError("OAuth authorization origin is not allowlisted by the signed manifest")
        state = str((parse_qs(parsed.query).get("state") or [""])[0]).strip()
        if not state:
            raise ValueError("OAuth authorization URL is missing state")
        future = asyncio.get_running_loop().create_future()
        nonce = secrets.token_urlsafe(24)
        with self._lock:
            previous = [item for item in self._flows.values() if item.server_name == server_name]
            self._flows[state] = _PendingOAuthFlow(server_name, state, nonce, future, _now_iso())
        for flow in previous:
            if not flow.future.done():
                flow.future.set_exception(RuntimeError("OAuth authorization superseded by a new request"))
            with self._lock:
                self._flows.pop(flow.state, None)
        self._set_status(
            server_name,
            "waiting_for_browser",
            stateFingerprint=state[:8],
            nonceFingerprint=nonce[:8],
        )
        opened = await asyncio.to_thread(webbrowser.open, authorization_url, 1, True)
        if not opened:
            self._set_status(server_name, "failed", error="system_browser_open_failed")
            raise RuntimeError("Failed to open the system browser for OAuth")

    async def _wait_for_callback(self, server_name: str, timeout_seconds: float) -> tuple[str, str | None]:
        with self._lock:
            flow = next((item for item in self._flows.values() if item.server_name == server_name), None)
        if flow is None:
            raise RuntimeError("OAuth redirect was not initialized")
        try:
            result = await asyncio.wait_for(asyncio.shield(flow.future), timeout=timeout_seconds)
            self._set_status(server_name, "exchanging_token")
            return result
        except asyncio.TimeoutError as exc:
            self._set_status(server_name, "timed_out", error="oauth_callback_timeout")
            raise RuntimeError("OAuth callback timed out") from exc
        finally:
            with self._lock:
                self._flows.pop(flow.state, None)

    def complete(self, *, code: str, state: str, error: str = "") -> dict[str, Any]:
        normalized_state = str(state or "").strip()
        with self._lock:
            flow = self._flows.get(normalized_state)
            if flow is None or flow.completed or flow.future.done():
                return {"ok": False, "status": "invalid_state"}
            # Consume state synchronously before scheduling work on the event
            # loop. Every callback, including a malformed callback without a
            # code, is one-shot and must wake the waiting OAuth flow.
            flow.completed = True
        if error:
            flow.future.get_loop().call_soon_threadsafe(
                flow.future.set_exception,
                RuntimeError(f"OAuth authorization failed: {error}"),
            )
            self._set_status(flow.server_name, "cancelled", error=error)
            return {"ok": False, "status": "cancelled", "serverName": flow.server_name}
        normalized_code = str(code or "").strip()
        if not normalized_code:
            flow.future.get_loop().call_soon_threadsafe(
                flow.future.set_exception,
                RuntimeError("OAuth callback did not include an authorization code"),
            )
            self._set_status(flow.server_name, "failed", error="oauth_callback_missing_code")
            return {"ok": False, "status": "missing_code"}
        if not flow.future.done():
            flow.future.get_loop().call_soon_threadsafe(flow.future.set_result, (normalized_code, normalized_state))
        return {"ok": True, "status": "callback_received", "serverName": flow.server_name}

    def cancel(self, server_name: str) -> dict[str, Any]:
        with self._lock:
            flows = [item for item in self._flows.values() if item.server_name == server_name]
        for flow in flows:
            with self._lock:
                self._flows.pop(flow.state, None)
            if not flow.future.done():
                flow.future.get_loop().call_soon_threadsafe(
                    flow.future.set_exception,
                    RuntimeError("OAuth authorization cancelled"),
                )
        self._set_status(server_name, "cancelled", error="cancelled_by_user")
        return {"ok": True, "serverName": server_name, "cancelled": len(flows)}

    def provider_for(self, *, server_name: str, server_url: str, config: dict[str, Any]) -> OAuthClientProvider:
        allowed_domains = {
            str(value or "").strip().lower().lstrip(".")
            for value in list(config.get("x-v8-oauth-allowed-domains") or [])
            if str(value or "").strip()
        }
        server_host = str(urlparse(server_url).hostname or "").lower()
        if not allowed_domains or not _allowed_host(server_host, allowed_domains):
            raise ValueError("OAuth MCP server origin is not allowlisted by the signed manifest")
        oauth_state = config.get("x-v8-oauth") if isinstance(config.get("x-v8-oauth"), dict) else {}
        token_storage = CredentialOAuthTokenStorage(
            server_name=server_name,
            plugin_id=str(config.get("x-v8-plugin-owner") or oauth_state.get("pluginId") or "").strip(),
            component_id=str(config.get("x-v8-plugin-component") or oauth_state.get("componentId") or "").strip(),
            secret_ref=str(oauth_state.get("secretRef") or "").strip() or None,
        )
        timeout = 300.0

        async def redirect_handler(url: str) -> None:
            await self._register_redirect(server_name, url, allowed_domains)

        async def callback_handler() -> tuple[str, str | None]:
            return await self._wait_for_callback(server_name, timeout)

        self._set_status(server_name, "connecting")
        return OAuthClientProvider(
            server_url=server_url,
            client_metadata=OAuthClientMetadata(
                redirect_uris=[AnyUrl(OAUTH_CALLBACK_URL)],
                token_endpoint_auth_method="none",
                client_name="V8 Agent OS",
                software_id="v8-agent-os",
                software_version="1",
            ),
            storage=token_storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=timeout,
        )


mcp_oauth_coordinator = McpOAuthCoordinator()


__all__ = [
    "CredentialOAuthTokenStorage",
    "McpOAuthCoordinator",
    "OAUTH_CALLBACK_URL",
    "mcp_oauth_coordinator",
]
