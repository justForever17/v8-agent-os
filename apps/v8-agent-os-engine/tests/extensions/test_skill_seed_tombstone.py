from __future__ import annotations

import json

from runtimes.extensions.skills.loader import SkillLoader


def test_seed_source_without_skill_md_is_not_copied(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / ".agents" / "skills" / "code-reviewer"
    source.mkdir(parents=True)
    (source / "references").mkdir()
    global_root = tmp_path / "home" / ".agents" / "skills"
    global_root.mkdir(parents=True)

    monkeypatch.setattr(SkillLoader, "_resolve_repo_root", classmethod(lambda cls: repo))
    monkeypatch.setattr(SkillLoader, "_skill_seed_tombstone_path", classmethod(lambda cls: tmp_path / "tombstones.json"))

    SkillLoader._ensure_seeded_global_skills(global_root)

    assert not (global_root / "code-reviewer").exists()


def test_invalid_empty_seed_shell_is_removed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / ".agents" / "skills" / "code-reviewer"
    source.mkdir(parents=True)
    global_root = tmp_path / "home" / ".agents" / "skills"
    shell = global_root / "code-reviewer"
    (shell / "references").mkdir(parents=True)
    (shell / "scripts").mkdir()

    monkeypatch.setattr(SkillLoader, "_resolve_repo_root", classmethod(lambda cls: repo))
    monkeypatch.setattr(SkillLoader, "_skill_seed_tombstone_path", classmethod(lambda cls: tmp_path / "tombstones.json"))

    SkillLoader._ensure_seeded_global_skills(global_root)

    assert not shell.exists()


def test_seed_tombstone_prevents_reseeding_valid_source(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source = repo / ".agents" / "skills" / "code-reviewer"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# Code Reviewer\n", encoding="utf-8")
    global_root = tmp_path / "home" / ".agents" / "skills"
    global_root.mkdir(parents=True)
    tombstone_path = tmp_path / "tombstones.json"

    monkeypatch.setattr(SkillLoader, "_resolve_repo_root", classmethod(lambda cls: repo))
    monkeypatch.setattr(SkillLoader, "_skill_seed_tombstone_path", classmethod(lambda cls: tombstone_path))

    tombstone = SkillLoader._record_skill_seed_tombstone(
        skill_name="code-reviewer",
        source_dir=source,
        target_dir=global_root / "code-reviewer",
        initiated_by="unit_test",
    )
    SkillLoader._ensure_seeded_global_skills(global_root)

    assert tombstone["skillName"] == "code-reviewer"
    assert not (global_root / "code-reviewer").exists()
    payload = json.loads(tombstone_path.read_text(encoding="utf-8"))
    assert payload["entries"]["code-reviewer"]["initiatedBy"] == "unit_test"
