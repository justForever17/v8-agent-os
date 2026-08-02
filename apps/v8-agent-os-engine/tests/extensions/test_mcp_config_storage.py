from __future__ import annotations

import json
import threading
from pathlib import Path

from core import storage as storage_module


def _storage_for_tmp(tmp_path: Path, monkeypatch) -> storage_module.StorageManager:
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(storage_module, "MCP_JSON_PATH", tmp_path / "mcp.json")
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = tmp_path
    manager._config_io_lock = threading.RLock()
    manager._mcp_io_lock = threading.RLock()
    manager._legacy_model_bindings_migrated = False
    manager._config_payload_cache_signature = None
    manager._config_payload_cache_data = None
    return manager


def test_mcp_config_reads_dedicated_mcp_json(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"context7": {"type": "http", "url": "https://mcp.context7.com/mcp"}}}),
        encoding="utf-8",
    )

    config = manager.get_mcp_config()

    assert config["mcpServers"]["context7"]["type"] == "http"


def test_mcp_config_projects_legacy_domain_without_write_on_read(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"mcp": {"mcpServers": {"sqlite": {"type": "stdio", "command": "openai-dev-mcp"}}}}),
        encoding="utf-8",
    )

    config = manager.get_mcp_config()

    assert config["mcpServers"]["sqlite"]["command"] == "openai-dev-mcp"
    assert not (tmp_path / "mcp.json").exists()


def test_explicit_mcp_migration_persists_legacy_domain(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"mcp": {"mcpServers": {"sqlite": {"type": "stdio", "command": "openai-dev-mcp"}}}}),
        encoding="utf-8",
    )

    assert manager.migrate_legacy_mcp_config() is True
    saved = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert saved["mcpServers"]["sqlite"]["type"] == "stdio"


def test_mcp_config_save_writes_only_mcp_json(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(json.dumps({"mcp": {"mcpServers": {"old": {"type": "http", "url": "https://old.test"}}}}), encoding="utf-8")

    manager.save_mcp_config({"mcpServers": {"new": {"type": "sse", "url": "https://new.test/sse"}}})

    dedicated = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    canonical = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert dedicated["mcpServers"]["new"]["type"] == "sse"
    assert "mcpServers" not in canonical
    assert canonical["mcp"]["mcpServers"]["old"]["url"] == "https://old.test"
