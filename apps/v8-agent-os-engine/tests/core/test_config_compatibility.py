from __future__ import annotations

import json
from pathlib import Path

from core import storage as storage_module
from core.agents import DEPRECATED_DEFAULT_SUBAGENT_IDS


def _storage_for_tmp(tmp_path: Path, monkeypatch) -> storage_module.StorageManager:
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = tmp_path
    manager._legacy_model_bindings_migrated = False
    manager._config_payload_cache_signature = None
    manager._config_payload_cache_data = None
    return manager


def test_unknown_config_domains_are_ignored_without_rewriting_source(tmp_path: Path, monkeypatch, capsys) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    raw = {
        "runtimeRegistry": {"installProfile": "minimal"},
        "removedRuntime": {"secret": "must-not-appear-in-diagnostics"},
    }
    config_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    before = config_path.read_bytes()

    effective = manager._read_config_payload()

    output = capsys.readouterr().out
    assert "removedRuntime" in output
    assert "must-not-appear-in-diagnostics" not in output
    assert "removedRuntime" not in effective
    assert effective["runtimeRegistry"]["installProfile"] == "minimal"
    assert config_path.read_bytes() == before


def test_known_config_write_preserves_ignored_domains_on_disk(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtimeRegistry": {"installProfile": "minimal"},
                "removedRuntime": {"preserved": True},
            }
        ),
        encoding="utf-8",
    )

    effective = manager._read_config_payload()
    effective["runtimeRegistry"]["startupProfile"] = "desktop"
    manager._write_config_payload(effective)

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["removedRuntime"] == {"preserved": True}
    assert persisted["runtimeRegistry"]["startupProfile"] == "desktop"


def test_stock_prompt_sanitizer_removes_retired_plugin_host_wording() -> None:
    legacy_runtime_id = "plugin_host"
    source = (
        "- Use product words with users: 插件桥接.\n"
        f"- Use route-selected skills / MCP / {legacy_runtime_id} candidates instead of exploring every tool family at once.\n"
        f"- Subagents should inherit relevant skills, MCP, {legacy_runtime_id}, and baseline tool context instead of starting blind.\n"
    )

    sanitized = storage_module._sanitize_stock_supervisor_prompt_text(source)

    assert "插件桥接" not in sanitized
    assert legacy_runtime_id not in sanitized
    assert "插件管理中心" in sanitized
    assert "explicit plugin grants" in sanitized


def test_retired_project_planner_binding_is_removed_after_managed_file_disappears(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "models": {
                    "bindings": {
                        "agents": {
                            "project-planner": "model-planner",
                            "verification-engineer": "model-verifier",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    manager._remove_deprecated_subagent_model_bindings()

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert "project-planner" in DEPRECATED_DEFAULT_SUBAGENT_IDS
    assert "project-planner" not in persisted["models"]["bindings"]["agents"]
    assert persisted["models"]["bindings"]["agents"]["verification-engineer"] == "model-verifier"


def test_custom_agent_file_preserves_same_id_model_binding(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "project-planner.md").write_text("# User-authored agent\n", encoding="utf-8")
    config_path.write_text(
        json.dumps({"models": {"bindings": {"agents": {"project-planner": "custom-model"}}}}),
        encoding="utf-8",
    )

    manager._remove_deprecated_subagent_model_bindings()

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["models"]["bindings"]["agents"]["project-planner"] == "custom-model"
