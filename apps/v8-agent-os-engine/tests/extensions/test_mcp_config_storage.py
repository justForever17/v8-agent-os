from __future__ import annotations

import json
from pathlib import Path

from core import storage as storage_module


def _storage_for_tmp(tmp_path: Path, monkeypatch) -> storage_module.StorageManager:
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    monkeypatch.setattr(storage_module, "MCP_JSON_PATH", tmp_path / "mcp.json")
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = tmp_path
    manager._legacy_model_bindings_migrated = False
    return manager


def test_mcp_config_reads_dedicated_mcp_json(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"context7": {"type": "http", "url": "https://mcp.context7.com/mcp"}}}),
        encoding="utf-8",
    )

    config = manager.get_mcp_config()

    assert config["mcpServers"]["context7"]["type"] == "http"


def test_mcp_config_migrates_legacy_config_domain_to_mcp_json(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(
        json.dumps({"mcp": {"mcpServers": {"sqlite": {"type": "stdio", "command": "openai-dev-mcp"}}}}),
        encoding="utf-8",
    )

    config = manager.get_mcp_config()

    assert config["mcpServers"]["sqlite"]["command"] == "openai-dev-mcp"
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
