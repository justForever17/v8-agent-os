from __future__ import annotations

import asyncio

from fastapi import HTTPException

from api import platform_routes


class _FakeStorage:
    def __init__(self) -> None:
        self.config = {
            "mcpServers": {
                "context7": {"url": "https://mcp.context7.com/mcp"},
                "keep": {"command": "uvx", "args": ["demo"]},
            }
        }
        self.saved: dict | None = None

    def get_mcp_config(self) -> dict:
        return self.config

    def save_mcp_config(self, config: dict) -> None:
        self.saved = config
        self.config = config


class _FakeMcpManager:
    def __init__(self, removed_by_reload: bool) -> None:
        self.removed_by_reload = removed_by_reload
        self.remove_calls: list[str] = []

    async def reload_if_changed(self) -> dict:
        removed = ["context7"] if self.removed_by_reload else []
        return {"mcpChangedServers": {"removed": removed}}

    async def remove_server(self, server_name: str) -> dict:
        self.remove_calls.append(server_name)
        return {"changed": True, "server": server_name}


class _FakeExtensionsRuntimeService:
    def __init__(self) -> None:
        self.refresh_reasons: list[str] = []

    async def force_refresh_after_mcp_config_change(self, *, reason: str, mcp_change: dict | None = None) -> dict:
        self.refresh_reasons.append(reason)
        return {"changed": True, "reason": reason, "mcp": mcp_change or {}}

    def build_health(self) -> dict:
        return {"status": "ok"}


def test_delete_mcp_config_server_removes_config_and_uses_delta_reload(monkeypatch):
    fake_storage = _FakeStorage()
    fake_manager = _FakeMcpManager(removed_by_reload=True)
    fake_extensions = _FakeExtensionsRuntimeService()
    monkeypatch.setattr(platform_routes, "storage", fake_storage)
    monkeypatch.setattr(platform_routes, "mcp_manager", fake_manager)
    monkeypatch.setattr(platform_routes, "extensions_runtime_service", fake_extensions)

    result = asyncio.run(platform_routes.delete_mcp_config_server("context7"))

    assert result["status"] == "success"
    assert result["deletedServer"] == "context7"
    assert "context7" not in fake_storage.saved["mcpServers"]
    assert "keep" in fake_storage.saved["mcpServers"]
    assert fake_manager.remove_calls == []
    assert result["removeResult"]["reason"] == "delta_reload_removed"
    assert fake_extensions.refresh_reasons == ["mcp_config_delete"]


def test_delete_mcp_config_server_calls_remove_when_reload_did_not_remove(monkeypatch):
    fake_storage = _FakeStorage()
    fake_manager = _FakeMcpManager(removed_by_reload=False)
    fake_extensions = _FakeExtensionsRuntimeService()
    monkeypatch.setattr(platform_routes, "storage", fake_storage)
    monkeypatch.setattr(platform_routes, "mcp_manager", fake_manager)
    monkeypatch.setattr(platform_routes, "extensions_runtime_service", fake_extensions)

    result = asyncio.run(platform_routes.delete_mcp_config_server("context7"))

    assert fake_manager.remove_calls == ["context7"]
    assert result["removeResult"]["server"] == "context7"


def test_delete_mcp_config_server_is_idempotent_for_missing_config_but_cleans_runtime(monkeypatch):
    fake_storage = _FakeStorage()
    fake_manager = _FakeMcpManager(removed_by_reload=False)
    fake_extensions = _FakeExtensionsRuntimeService()
    monkeypatch.setattr(platform_routes, "storage", fake_storage)
    monkeypatch.setattr(platform_routes, "mcp_manager", fake_manager)
    monkeypatch.setattr(platform_routes, "extensions_runtime_service", fake_extensions)

    result = asyncio.run(platform_routes.delete_mcp_config_server("missing"))

    assert result["status"] == "success"
    assert result["deletedServer"] == "missing"
    assert result["alreadyRemovedFromConfig"] is True
    assert fake_manager.remove_calls == ["missing"]
    assert fake_extensions.refresh_reasons == ["mcp_config_delete"]
