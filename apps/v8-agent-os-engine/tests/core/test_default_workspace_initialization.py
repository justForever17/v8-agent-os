from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import core.storage as storage_module


def _manager(root: Path) -> storage_module.StorageManager:
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = root / "state"
    return manager


def test_default_workspace_is_only_created_for_fresh_initialization(tmp_path: Path):
    default_workspace = tmp_path / "state" / "workspace"
    runtime_root = tmp_path / "runtime"
    manager = _manager(tmp_path)

    with (
        patch.object(storage_module, "WORKSPACE_HOME", default_workspace),
        patch.object(storage_module, "RUNTIME_DATA_HOME", runtime_root),
        patch.object(storage_module, "runtime_private_root", side_effect=lambda name: runtime_root / name),
        patch.object(manager, "_ensure_default_subagents"),
        patch.object(manager, "_ensure_config_json_exists"),
        patch.object(manager, "_remove_deprecated_subagent_model_bindings"),
        patch.object(manager, "_migrate_computer_use_storage"),
        patch.object(manager, "_migrate_legacy_structured_files"),
    ):
        manager._initialize_structure(initialize_default_workspace=False)
        assert not default_workspace.exists()

        manager._initialize_structure(initialize_default_workspace=True)
        assert default_workspace.is_dir()


def test_fresh_config_points_to_bundled_workspace_not_global_memory():
    workspace_config = storage_module.STRUCTURED_CONFIG_DEFAULTS["workspace"]
    assert Path(workspace_config["agent_workspace_path"]) == storage_module.WORKSPACE_HOME
    assert workspace_config["agent_workspace_path"] != "global"
