from __future__ import annotations

from core.agents import default_subagent_configs, dump_agent_md, parse_agent_md
from core.storage import StorageManager


def test_managed_default_refreshes_when_its_content_fingerprint_changes(tmp_path):
    manager = StorageManager.__new__(StorageManager)
    manager.base_dir = tmp_path
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)

    desired = next(
        agent for agent in default_subagent_configs() if agent.id == "creative-media-director"
    )
    stale = desired.model_copy(
        update={
            "defaultTemplateVersion": "v8-default-subagents-stale",
            "system_prompt": "STALE_PLANNING_ONLY_PROMPT",
        }
    )
    target = agents_dir / "creative-media-director.md"
    target.write_text(dump_agent_md(stale), encoding="utf-8")

    manager._ensure_default_subagents()

    refreshed = parse_agent_md(target.read_text(encoding="utf-8"), target.name)
    assert refreshed.defaultTemplateVersion == desired.defaultTemplateVersion
    assert refreshed.system_prompt == desired.system_prompt
    assert "then execute that plan" in refreshed.system_prompt
    backups = list((tmp_path / "backups" / "agents").glob("*/creative-media-director.md"))
    assert len(backups) == 1
    assert "STALE_PLANNING_ONLY_PROMPT" in backups[0].read_text(encoding="utf-8")

    refreshed_mtime = target.stat().st_mtime_ns
    manager._ensure_default_subagents()
    assert target.stat().st_mtime_ns == refreshed_mtime
    assert len(list((tmp_path / "backups" / "agents").glob("*/creative-media-director.md"))) == 1


def test_user_authored_agent_with_default_name_is_not_replaced(tmp_path):
    manager = StorageManager.__new__(StorageManager)
    manager.base_dir = tmp_path
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)

    desired = next(
        agent for agent in default_subagent_configs() if agent.id == "creative-media-director"
    )
    custom = desired.model_copy(
        update={
            "createdBy": "user",
            "defaultTemplateVersion": "",
            "capabilitySnapshot": {"source": "user"},
            "system_prompt": "USER_AUTHORED_CREATIVE_DIRECTOR",
        }
    )
    target = agents_dir / "creative-media-director.md"
    target.write_text(dump_agent_md(custom), encoding="utf-8")

    manager._ensure_default_subagents()

    preserved = parse_agent_md(target.read_text(encoding="utf-8"), target.name)
    assert preserved.system_prompt == "USER_AUTHORED_CREATIVE_DIRECTOR"
    assert not (tmp_path / "backups" / "agents").exists()
