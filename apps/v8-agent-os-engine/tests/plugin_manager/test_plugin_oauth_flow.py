from __future__ import annotations

import asyncio

import pytest
from mcp.shared.auth import OAuthToken

from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from runtimes.extensions.mcp.oauth import CredentialOAuthTokenStorage, McpOAuthCoordinator


def test_oauth_callback_state_is_one_time_and_browser_origin_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        coordinator = McpOAuthCoordinator()
        monkeypatch.setattr("runtimes.extensions.mcp.oauth.webbrowser.open", lambda *_args, **_kwargs: True)

        await coordinator._register_redirect(
            "figma",
            "https://www.figma.com/oauth?state=state-1",
            {"figma.com"},
        )
        assert coordinator.status("figma")["status"] == "waiting_for_browser"
        completed = coordinator.complete(code="code-1", state="state-1")
        assert completed["ok"] is True
        assert coordinator.complete(code="code-2", state="state-1")["status"] == "invalid_state"
        assert await coordinator._wait_for_callback("figma", 0.2) == ("code-1", "state-1")

        with pytest.raises(ValueError, match="allowlisted"):
            await coordinator._register_redirect(
                "figma",
                "https://figma.com.evil.example/oauth?state=state-2",
                {"figma.com"},
            )

    asyncio.run(scenario())


def test_oauth_callback_without_code_consumes_state_and_releases_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        coordinator = McpOAuthCoordinator()
        monkeypatch.setattr("runtimes.extensions.mcp.oauth.webbrowser.open", lambda *_args, **_kwargs: True)
        await coordinator._register_redirect(
            "figma",
            "https://www.figma.com/oauth?state=missing-code-state",
            {"figma.com"},
        )
        waiter = asyncio.create_task(coordinator._wait_for_callback("figma", 0.2))

        first = coordinator.complete(code="", state="missing-code-state")
        second = coordinator.complete(code="late-code", state="missing-code-state")

        assert first["status"] == "missing_code"
        assert second["status"] == "invalid_state"
        with pytest.raises(RuntimeError, match="authorization code"):
            await waiter

    asyncio.run(scenario())


def test_oauth_token_storage_persists_only_opaque_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    class Storage:
        def __init__(self) -> None:
            self.payload = {"mcpServers": {"figma": {"url": "https://mcp.figma.com/mcp"}}}

        def get_mcp_config(self):
            return self.payload

        def save_mcp_config(self, value):
            self.payload = value

    async def scenario() -> None:
        fake_storage = Storage()
        monkeypatch.setattr("runtimes.extensions.mcp.oauth.storage", fake_storage)
        monkeypatch.setattr(
            "runtimes.plugin_manager.service.plugin_manager_service.refresh_configuration_status",
            lambda _plugin_id: {},
        )
        credential_store = CredentialRefStore(MemoryCredentialBackend())
        token_storage = CredentialOAuthTokenStorage(
            server_name="figma",
            plugin_id="figma",
            component_id="figma-remote-mcp",
            secret_ref=None,
            credential_store=credential_store,
        )
        await token_storage.set_tokens(OAuthToken(access_token="top-secret-token", token_type="bearer"))

        server = fake_storage.payload["mcpServers"]["figma"]
        assert "top-secret-token" not in str(fake_storage.payload)
        assert server["x-v8-oauth"]["secretRef"].startswith("cred:v8-plugin:")
        assert (await token_storage.get_tokens()).access_token == "top-secret-token"

    asyncio.run(scenario())
