from __future__ import annotations

import json
import threading
from pathlib import Path

from core import storage as storage_module
from core.agents import DEPRECATED_DEFAULT_SUBAGENT_IDS


def _storage_for_tmp(tmp_path: Path, monkeypatch) -> storage_module.StorageManager:
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = tmp_path
    manager._config_io_lock = storage_module._CONFIG_IO_LOCK
    manager._mcp_io_lock = storage_module._MCP_IO_LOCK
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


def test_config_write_keeps_the_previous_payload_as_a_recoverable_preimage(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtimeRegistry": {"installProfile": "minimal"},
                "models": {"providers": {"before": {"models": {}}}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    before = config_path.read_bytes()

    manager._write_config_payload(
        {
            "runtimeRegistry": {"installProfile": "desktop"},
            "models": {"providers": {"after": {"models": {}}}},
        }
    )

    backup = tmp_path / "backups" / "json" / "config.json.bak"
    history = list((backup.parent / "history").glob("config.json.*.bak"))
    assert config_path.read_bytes() != before
    assert backup.read_bytes() == before
    assert len(history) == 1
    assert history[0].read_bytes() == before


def test_config_write_refuses_to_replace_live_config_when_preimage_backup_fails(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"runtimeRegistry": {"installProfile": "minimal"}}), encoding="utf-8")
    before = config_path.read_bytes()

    monkeypatch.setattr(manager, "_save_config_preimage", lambda _path: (_ for _ in ()).throw(OSError("backup unavailable")))

    try:
        manager._write_config_payload({"runtimeRegistry": {"installProfile": "desktop"}})
    except OSError as exc:
        assert str(exc) == "backup unavailable"
    else:
        raise AssertionError("config replacement must stop when the preimage backup cannot be written")
    assert config_path.read_bytes() == before


def test_preserved_domain_can_be_read_and_mutated_without_replacing_siblings(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "rpa": {"nativeInspector": {"enabled": True}, "keep": "value"},
                "removedRuntime": {"preserved": True},
            }
        ),
        encoding="utf-8",
    )

    assert manager.get_config_domain("rpa")["nativeInspector"]["enabled"] is True
    manager.mutate_config_domain(
        "rpa",
        lambda current: {
            **dict(current or {}),
            "nativeInspector": {"enabled": False},
        },
    )

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["rpa"] == {
        "nativeInspector": {"enabled": False},
        "keep": "value",
    }
    assert persisted["removedRuntime"] == {"preserved": True}


def test_config_read_uses_defaults_without_recreating_a_missing_file(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"

    assert manager.get_config_domain("ui") == {"theme": "system"}
    assert not config_path.exists()


def test_system_and_safety_reads_are_pure_and_explicit_migrations_are_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "systemBase": {"bridge": {"engineBaseUrl": "http://127.0.0.1:9530/v1"}},
                "safety": {"runtimeRules": {"allow": ["read"]}},
                "removedRuntime": {"preserved": True},
            }
        ),
        encoding="utf-8",
    )
    before = config_path.read_bytes()

    system_base = manager.get_system_base_config()
    repeated_system_base = manager.get_system_base_config()
    safety = manager.get_safety_guardian_config()

    assert system_base["bridge"]["internalSecret"]
    assert repeated_system_base["bridge"]["internalSecret"] == system_base["bridge"]["internalSecret"]
    assert safety["runtimeRules"]["allow"] == ["read"]
    assert config_path.read_bytes() == before
    assert manager.migrate_system_base_config() is True
    assert manager.migrate_safety_guardian_config() is False
    migrated = config_path.read_bytes()
    assert manager.migrate_system_base_config() is False
    assert manager.migrate_safety_guardian_config() is False
    assert config_path.read_bytes() == migrated
    assert json.loads(migrated)["removedRuntime"] == {"preserved": True}


def test_config_domain_mutations_share_one_lock_across_storage_instances(tmp_path: Path, monkeypatch) -> None:
    first = _storage_for_tmp(tmp_path, monkeypatch)
    second = storage_module.StorageManager()
    first.save_ui_config({"theme": "system"})
    first_mutator_entered = threading.Event()
    release_first = threading.Event()
    second_mutator_entered = threading.Event()

    def _first_mutator(_current):
        first_mutator_entered.set()
        assert release_first.wait(timeout=2)
        return {"theme": "dark"}

    def _second_mutator(_current):
        second_mutator_entered.set()
        return {"enabled": False}

    first_thread = threading.Thread(
        target=lambda: first.mutate_config_domain("ui", _first_mutator),
        daemon=True,
    )
    second_thread = threading.Thread(
        target=lambda: second.mutate_config_domain("automationRuntime", _second_mutator),
        daemon=True,
    )
    first_thread.start()
    assert first_mutator_entered.wait(timeout=2)
    second_thread.start()
    try:
        assert not second_mutator_entered.wait(timeout=0.2)
    finally:
        release_first.set()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert persisted["ui"]["theme"] == "dark"
    assert persisted["automationRuntime"]["enabled"] is False


def test_generic_json_mutation_preserves_unrelated_records(tmp_path: Path, monkeypatch) -> None:
    manager = _storage_for_tmp(tmp_path, monkeypatch)
    filename = "creative_media/model_preferences.json"
    manager.write_json(
        filename,
        {
            "selections": [
                {"operationKind": "image.generate", "enabled": True},
                {"operationKind": "video.reference_to_video", "enabled": False},
            ]
        },
    )

    mutated = manager.mutate_json(
        filename,
        lambda current: {
            **current,
            "selections": [
                item
                if item["operationKind"] != "video.reference_to_video"
                else {**item, "enabled": True}
                for item in current["selections"]
            ],
        },
    )

    assert mutated["selections"] == [
        {"operationKind": "image.generate", "enabled": True},
        {"operationKind": "video.reference_to_video", "enabled": True},
    ]
    assert manager.read_json(filename) == mutated


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
