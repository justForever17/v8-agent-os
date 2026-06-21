from __future__ import annotations

from core import mcp_config_service
from core.native_tools import mcp_server_config


class _FakeStorage:
    def __init__(self) -> None:
        self.payload = {"mcpServers": {}}

    def get_mcp_config(self):
        return self.payload

    def save_mcp_config(self, data):
        self.payload = data


class _FakeExtensionsRuntimeService:
    def __init__(self) -> None:
        self.refresh_reasons: list[str] = []

    def request_mcp_inventory_refresh(self, *, reason: str = "manual") -> None:
        self.refresh_reasons.append(reason)

    def get_mcp_status(self):
        return {"demo": {"status": "configured"}}

    def get_mcp_health_summary(self):
        return {"status": "ok"}

    def get_mcp_startup_status(self):
        return {"startupState": "ready"}


def test_mcp_server_config_install_list_remove(monkeypatch) -> None:
    fake_storage = _FakeStorage()
    fake_runtime = _FakeExtensionsRuntimeService()
    monkeypatch.setattr(mcp_config_service, "storage", fake_storage)
    monkeypatch.setattr(mcp_config_service, "extensions_runtime_service", fake_runtime)

    install_output = mcp_server_config.func(
        mode="mcp_install",
        name="demo",
        type="stdio",
        command="npx",
        args=["-y", "@demo/server"],
        env={"API_KEY": "secret-value"},
    )

    assert "已配置 MCP server" in install_output
    assert "secret-value" not in install_output
    assert fake_storage.payload["mcpServers"]["demo"]["type"] == "stdio"
    assert fake_storage.payload["mcpServers"]["demo"]["args"] == ["-y", "@demo/server"]
    assert fake_runtime.refresh_reasons == ["mcp_config_tool_install"]

    list_output = mcp_server_config.func(mode="mcp_list")
    assert "demo: stdio -> npx" in list_output
    assert "API_KEY" in list_output
    assert "secret-value" not in list_output

    remove_output = mcp_server_config.func(mode="mcp_remove", name="demo")
    assert "已移除 MCP server `demo`" in remove_output
    assert "demo" not in fake_storage.payload["mcpServers"]


def test_mcp_server_config_rejects_missing_type(monkeypatch) -> None:
    monkeypatch.setattr(mcp_config_service, "storage", _FakeStorage())
    monkeypatch.setattr(mcp_config_service, "extensions_runtime_service", _FakeExtensionsRuntimeService())

    output = mcp_server_config.func(mode="mcp_install", name="demo", command="npx")

    assert "MCP server 配置无效" in output
    assert "必须声明 type" in output
