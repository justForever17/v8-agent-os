from __future__ import annotations

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core import mcp_config_service
from core.interprocess_lock import interprocess_file_lock
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
        command_args=["-y", "@demo/server"],
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


class _SlowCopyingStorage:
    def __init__(self, *, fail_save: bool = False) -> None:
        self.payload = {"mcpServers": {}}
        self.fail_save = fail_save
        self._lock = threading.Lock()

    def get_mcp_config(self):
        with self._lock:
            snapshot = copy.deepcopy(self.payload)
        time.sleep(0.05)
        return snapshot

    def save_mcp_config(self, data):
        if self.fail_save:
            raise OSError("simulated atomic save failure")
        with self._lock:
            self.payload = copy.deepcopy(data)


def _stdio_server(package: str) -> dict:
    return {"type": "stdio", "command": "npx", "args": ["--yes", package]}


def test_different_mcp_installs_concurrently_preserve_both_servers(monkeypatch, tmp_path: Path) -> None:
    fake_storage = _SlowCopyingStorage()
    fake_runtime = _FakeExtensionsRuntimeService()
    start = threading.Barrier(2)
    monkeypatch.setattr(mcp_config_service, "storage", fake_storage)
    monkeypatch.setattr(mcp_config_service, "extensions_runtime_service", fake_runtime)
    monkeypatch.setattr(mcp_config_service, "_mcp_config_lock_path", lambda: tmp_path / "mcp-config.lock")

    def install(name: str) -> dict:
        start.wait(timeout=5)
        return mcp_config_service.install_mcp_server_config(
            {"mcpServers": {name: _stdio_server(f"@demo/{name}")}},
            refresh_reason=f"test_{name}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(install, ["alpha", "beta"]))

    assert set(fake_storage.payload["mcpServers"]) == {"alpha", "beta"}
    assert sorted(result["serverCount"] for result in results) == [1, 2]
    assert set(fake_runtime.refresh_reasons) == {"test_alpha", "test_beta"}


def test_mcp_save_failure_releases_lock_for_retry(monkeypatch, tmp_path: Path) -> None:
    fake_storage = _SlowCopyingStorage(fail_save=True)
    fake_runtime = _FakeExtensionsRuntimeService()
    lock_path = tmp_path / "mcp-config.lock"
    monkeypatch.setattr(mcp_config_service, "storage", fake_storage)
    monkeypatch.setattr(mcp_config_service, "extensions_runtime_service", fake_runtime)
    monkeypatch.setattr(mcp_config_service, "_mcp_config_lock_path", lambda: lock_path)

    with pytest.raises(OSError, match="simulated atomic save failure"):
        mcp_config_service.install_mcp_server_config(
            {"mcpServers": {"demo": _stdio_server("@demo/server")}},
        )

    with interprocess_file_lock(lock_path, timeout_seconds=0.2):
        pass
    fake_storage.fail_save = False
    result = mcp_config_service.install_mcp_server_config(
        {"mcpServers": {"demo": _stdio_server("@demo/server")}},
    )
    assert result["installedServers"] == ["demo"]
