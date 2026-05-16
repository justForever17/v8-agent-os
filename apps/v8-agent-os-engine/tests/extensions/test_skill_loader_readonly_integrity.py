from pathlib import Path

from erc.safety_guardian import safety_guardian
from runtimes.extensions.skills.loader import SkillLoader


def test_single_skill_scan_does_not_modify_skill_files(tmp_path, monkeypatch):
    import runtimes.extensions.skills.loader as loader_module

    skill_root = tmp_path / "demo-skill"
    skill_root.mkdir()
    skill_file = skill_root / "SKILL.md"
    content = """---
name: demo-skill
description: Use this for demo tasks.
---
# Demo Skill

Use this for demo tasks.
"""
    skill_file.write_text(content, encoding="utf-8")
    before = skill_file.stat()

    monkeypatch.setattr(loader_module, "annotate_skill_entries", lambda entries, record_reviews=True: entries)
    descriptor = {
        "rootPath": str(tmp_path),
        "sourceType": "global",
        "visibility": "global",
    }
    manifest = SkillLoader._compute_root_manifest(descriptor)
    manifest_item = manifest[str(skill_file)]

    entry = SkillLoader._scan_single_skill_descriptor(descriptor=descriptor, manifest_item=manifest_item)
    after = skill_file.stat()

    assert entry is not None
    assert skill_file.read_text(encoding="utf-8") == content
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns


def test_skill_delete_api_is_the_only_loader_path_that_removes_directory(tmp_path, monkeypatch):
    # This guards the intended contract at the loader level: refresh/scan paths read
    # skill roots, while deletion is isolated in delete_skill.
    source = Path(SkillLoader.delete_skill.__code__.co_filename).read_text(encoding="utf-8")

    assert "def delete_skill" in source
    assert "shutil.rmtree(skill_root)" in source
    assert "backupPath" in source


def test_safety_reviews_direct_skill_file_write(tmp_path):
    workspace = tmp_path / "workspace"
    skill_file = workspace / ".agents" / "skills" / "demo" / "SKILL.md"

    decision = safety_guardian.assess_file_write(
        str(skill_file),
        append=False,
        runtime_context={"workspace_path": str(workspace)},
    )

    assert decision.verdict == "review"
    assert decision.risk_code == "protected_skill_root_write"


def test_safety_blocks_skill_root_destructive_command(tmp_path):
    workspace = tmp_path / "workspace"
    command = f"Remove-Item -LiteralPath '{workspace / '.agents' / 'skills'}' -Recurse -Force"

    decision = safety_guardian.assess_system_command(
        command,
        runtime_context={"workspace_path": str(workspace)},
    )

    assert decision.verdict == "block"
    assert decision.risk_code == "protected_skill_root_destructive_command"


def test_safety_allows_regular_workspace_file_write(tmp_path):
    workspace = tmp_path / "workspace"
    target = workspace / "notes.md"

    decision = safety_guardian.assess_file_write(
        str(target),
        append=False,
        runtime_context={"workspace_path": str(workspace)},
    )

    assert decision.verdict == "allow"
    assert decision.risk_code == "workspace_file_write_allowed"
