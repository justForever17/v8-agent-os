from __future__ import annotations

from erc.runtime_context import bind_runtime_context
from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions


def test_fetch_skill_instructions_missing_skill_refreshes_workspace_root(tmp_path):
    workspace = tmp_path / "workspace"
    skill_name = "zzzzgeneratedworkspacealphaskill"
    skill_root = workspace / ".agents" / "skills" / skill_name
    workspace.mkdir(parents=True)

    assert not SkillLoader.resolve_skill_matches(
        skill_name,
        force_refresh=False,
        explicit_workspace_path=str(workspace),
    )

    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        f"""---
name: {skill_name}
description: A freshly generated workspace skill.
---

# Fresh Workspace Skill

## Use when
Use this skill when a same-session Engineering task has just generated it.
""",
        encoding="utf-8",
    )

    matches = SkillLoader.resolve_skill_matches(
        skill_name,
        force_refresh=False,
        explicit_workspace_path=str(workspace),
    )

    assert matches
    assert matches[0]["skillName"] == skill_name
    assert str(matches[0]["skillRoot"]).endswith(skill_name)


def test_missing_path_like_skill_identifier_does_not_fuzzy_refresh(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    matches = SkillLoader.resolve_skill_matches(
        "missing/path/skill",
        force_refresh=False,
        explicit_workspace_path=str(workspace),
    )

    assert matches == []


def test_missing_slug_like_skill_identifier_does_not_fuzzy_to_other_perspective(tmp_path):
    workspace = tmp_path / "workspace"
    existing = workspace / ".agents" / "skills" / "elon-musk-perspective"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text(
        """---
name: elon-musk-perspective
description: Existing perspective skill.
---

# Elon Musk Perspective

## Use when
Use for Musk-style reasoning.
""",
        encoding="utf-8",
    )

    SkillLoader.resolve_skill_matches(
        "elon-musk-perspective",
        force_refresh=True,
        explicit_workspace_path=str(workspace),
    )

    matches = SkillLoader.resolve_skill_matches(
        "sanyueqi-perspective",
        force_refresh=False,
        explicit_workspace_path=str(workspace),
    )

    assert matches == []


def test_fetch_skill_instructions_exposes_manifest_and_reads_relative_file(tmp_path):
    workspace = tmp_path / "workspace"
    skill_name = "continuation-skill"
    skill_root = workspace / ".agents" / "skills" / skill_name
    refs = skill_root / "references"
    refs.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        f"""---
name: {skill_name}
description: Skill with references.
---

# Continuation Skill

Read references/skill-template.md when creating artifacts.
""",
        encoding="utf-8",
    )
    (refs / "skill-template.md").write_text("# Template\n\nRequired YAML frontmatter.", encoding="utf-8")

    SkillLoader.resolve_skill_matches(
        skill_name,
        force_refresh=True,
        explicit_workspace_path=str(workspace),
    )

    with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
        full = fetch_skill_instructions.invoke(
            {
                "skill_name": skill_name,
                "detail_level": "full",
            }
        )
    assert "=== CONTINUATION MANIFEST ===" in full
    assert "references/skill-template.md" in full

    with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
        continuation = fetch_skill_instructions.invoke(
            {
                "skill_name": skill_name,
                "relative_path": "references/skill-template.md",
            }
        )
    assert "=== SKILL FILE ===" in continuation
    assert "Relative Path: references/skill-template.md" in continuation
    assert "Required YAML frontmatter" in continuation
