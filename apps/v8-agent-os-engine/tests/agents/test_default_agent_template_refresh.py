from __future__ import annotations

from pathlib import Path

import pytest

from core.agents import default_subagent_configs, dump_agent_md, parse_agent_md
from core.storage import StorageManager


LEGACY = Path(__file__).parents[1] / "fixtures/agents/creative-media-director-9.16.4.md"


def manager_at(root):
    manager = StorageManager.__new__(StorageManager)
    manager.base_dir = root
    (root / "agents").mkdir(parents=True)
    return manager


def director():
    return next(a for a in default_subagent_configs() if a.id == "creative-media-director")


def test_exact_released_seed_migrates_with_backup_and_is_idempotent(tmp_path):
    manager = manager_at(tmp_path)
    target = tmp_path / "agents/creative-media-director.md"
    original = LEGACY.read_text(encoding="utf-8").replace("\n", "\r\n").encode()
    target.write_bytes(original)
    manager._ensure_default_subagents()
    assert target.read_text(encoding="utf-8") == dump_agent_md(director())
    backup = list((tmp_path / "backups/agents").glob("*/creative-media-director.md"))
    assert len(backup) == 1 and backup[0].read_bytes() == original
    mtime = target.stat().st_mtime_ns
    manager._ensure_default_subagents()
    assert target.stat().st_mtime_ns == mtime
    assert len(list((tmp_path / "backups/agents").glob("*/creative-media-director.md"))) == 1


@pytest.mark.parametrize("edit", [
    lambda s: s + "\nMy own instruction: preserve hand-drawn ink texture.\n",
    lambda s: s.replace("roleLabel: Creative Director", "roleLabel: My director"),
    lambda s: s.replace("tools: []", "tools: [read_native_file]"),
    lambda s: s.replace("---\n", "---\n# My config comment\n", 1),
    lambda s: s.replace("2026-07-26", "unknown-release"),
])
def test_edit_retaining_system_and_version_markers_is_preserved_byte_for_byte(tmp_path, edit):
    manager = manager_at(tmp_path)
    target = tmp_path / "agents/creative-media-director.md"
    original = edit(LEGACY.read_text(encoding="utf-8")).encode()
    assert original != LEGACY.read_text(encoding="utf-8").encode()
    target.write_bytes(original)
    manager._ensure_default_subagents()
    assert target.read_bytes() == original
    assert not (tmp_path / "backups/agents").exists()


def test_fresh_files_are_professional_prose_and_reload_builtin_identity(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    manager._ensure_default_subagents()
    monkeypatch.setattr(manager, "get_agent_model_bindings", lambda: {"motion-shot-director": "configured/model"})
    loaded = {a["id"]: a for a in manager.get_all_agents()}
    for agent in default_subagent_configs():
        body = (tmp_path / "agents" / f"{agent.id}.md").read_text(encoding="utf-8")
        assert not body.startswith("---")
        assert body.strip() == agent.system_prompt
        for internal in ("V8", "runtime", "creative_media_", "delegation_broker", "runtimeBindings", "grantGroups", "supervisor", "defaultTemplateVersion"):
            assert internal not in body, (agent.id, internal)
        assert loaded[agent.id]["capabilitySnapshot"] == agent.capabilitySnapshot
        assert loaded[agent.id]["tool_mode"] == "contextual_auto"
        assert loaded[agent.id]["system_prompt"] == agent.system_prompt
    assert loaded["motion-shot-director"]["model"] == "configured/model"


def test_modified_new_seed_loads_and_explicit_default_save_resets_prose(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    manager._ensure_default_subagents()
    target = tmp_path / "agents/creative-media-director.md"
    custom = "Work in charcoal only; keep the user's robot geometry.\n"
    target.write_text(custom, encoding="utf-8")
    monkeypatch.setattr(manager, "get_agent_model_binding", lambda _id: "configured/model")
    bindings = []
    monkeypatch.setattr(manager, "set_agent_model_binding", lambda agent_id, model: bindings.append((agent_id, model)))
    manager._ensure_default_subagents()
    assert target.read_text(encoding="utf-8") == custom
    loaded = manager.get_agent("creative-media-director")
    assert loaded["system_prompt"] == custom.strip()
    assert loaded["capabilitySnapshot"]["runtimeBindings"] == director().capabilitySnapshot["runtimeBindings"]
    # Explicit reset through the existing save owner; startup never resets edits.
    manager.save_agent({**loaded, "system_prompt": director().system_prompt})
    assert target.read_text(encoding="utf-8") == dump_agent_md(director())
    assert bindings == [("creative-media-director", "configured/model")]
    manager._ensure_default_subagents()
    assert manager.get_agent("creative-media-director")["system_prompt"] == director().system_prompt


def test_unknown_retired_or_renamed_files_are_not_deleted_by_markers(tmp_path):
    manager = manager_at(tmp_path)
    content = LEGACY.read_text(encoding="utf-8")
    paths = [tmp_path / "agents/project-planner.md", tmp_path / "agents/my-director.md"]
    for path in paths:
        path.write_text(content, encoding="utf-8")
    manager._ensure_default_subagents()
    assert all(path.read_text(encoding="utf-8") == content for path in paths)


def test_explicit_custom_metadata_remains_config_not_prompt():
    default = director()
    custom = default.model_copy(update={"tools": [], "tool_mode": "explicit", "capabilitySnapshot": {"specialistFamily": "freelancers", "runtimeBindings": []}})
    parsed = parse_agent_md(dump_agent_md(custom), custom.id + ".md")
    assert parsed.tool_mode == "explicit"
    assert parsed.tools == []
    assert parsed.capabilitySnapshot["runtimeBindings"] == []
    assert parsed.system_prompt == default.system_prompt


def test_legacy_tool_mode_alias_overrides_builtin_default_without_widening_tools():
    from graph.agent_factories import _resolved_tool_mode
    parsed = parse_agent_md("---\ntoolMode: explicit\ntools: []\n---\nMy own verification method.\n", "verification-engineer.md")
    assert parsed.tool_mode == "explicit"
    assert parsed.tools == []
    assert _resolved_tool_mode(parsed.model_dump()) == "explicit"


def test_failed_migration_publication_keeps_original_and_recoverable_backup(tmp_path, monkeypatch):
    manager = manager_at(tmp_path)
    target = tmp_path / "agents/creative-media-director.md"
    original = LEGACY.read_bytes()
    target.write_bytes(original)

    def fail_publish(*_args, **_kwargs):
        raise PermissionError("synthetic blocked replace")

    monkeypatch.setattr(manager, "_replace_json_file", fail_publish)
    manager._ensure_default_subagents()
    assert target.read_bytes() == original
    assert next((tmp_path / "backups/agents").glob("*/creative-media-director.md")).read_bytes() == original
    assert not list(target.parent.glob("*.tmp"))


def test_edit_during_backup_is_preserved_before_publication(tmp_path, monkeypatch):
    import core.storage as module
    manager = manager_at(tmp_path)
    target = tmp_path / "agents/creative-media-director.md"
    target.write_bytes(LEGACY.read_bytes())
    copy = module.shutil.copy2
    changed = b"User edited during upgrade; preserve this text.\n"

    def concurrent_edit(source, destination):
        result = copy(source, destination)
        target.write_bytes(changed)
        return result

    monkeypatch.setattr(module.shutil, "copy2", concurrent_edit)
    manager._ensure_default_subagents()
    assert target.read_bytes() == changed
    assert not list(target.parent.glob("*.tmp"))


def test_unreadable_encoding_is_preserved_without_breaking_other_seeds(tmp_path):
    manager = manager_at(tmp_path)
    target = tmp_path / "agents/creative-media-director.md"
    original = b"\xff\xfeinvalid-custom-encoding"
    target.write_bytes(original)
    manager._ensure_default_subagents()
    assert target.read_bytes() == original
    assert (tmp_path / "agents/motion-shot-director.md").is_file()


def test_edit_during_windows_replace_backoff_is_not_overwritten(tmp_path, monkeypatch):
    import core.storage as module
    manager = manager_at(tmp_path)
    target = tmp_path / "agents/creative-media-director.md"
    target.write_bytes(LEGACY.read_bytes())
    actual_replace = module.os.replace
    calls = []
    edited = b"My edit saved while another Windows reader released the file.\n"

    def replace_with_intervening_edit(source, destination):
        calls.append(destination)
        if len(calls) == 1:
            target.write_bytes(edited)
            error = PermissionError("synthetic Windows sharing violation")
            error.winerror = 32
            raise error
        actual_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", replace_with_intervening_edit)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    manager._ensure_default_subagents()
    assert target.read_bytes() == edited
    assert calls == [target]
