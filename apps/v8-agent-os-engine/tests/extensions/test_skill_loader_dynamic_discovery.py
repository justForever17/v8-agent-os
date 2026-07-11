from __future__ import annotations

from core.workspace_authority import WorkspaceAuthorityDescriptor
from erc.runtime_context import bind_runtime_context
from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions
from unittest.mock import patch


def _trusted_workspace_authority(workspace) -> WorkspaceAuthorityDescriptor:
    return WorkspaceAuthorityDescriptor(
        runtime_kind="chat",
        workspace_id="",
        project_id="",
        workspace_root=str(workspace),
        main_workspace_root=str(workspace),
        source="main_workspace",
        trust_state="trusted",
        trust_source="test_explicit_trust",
        uses_scoped_workspace=False,
        is_scoped_override=False,
        is_fallback_to_main=False,
        side_effects_allowed=True,
        capabilities={
            "localRead": True,
            "localWrite": True,
            "commandCwd": True,
            "upload": True,
            "workspaceRules": True,
            "externalWorker": True,
        },
        path_status={},
    )


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
    scripts = skill_root / "scripts"
    scripts.mkdir(parents=True)
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
    (scripts / "check-quality.py").write_text(
        "import sys\nprint('ok:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )

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
    assert "Relative Resources:" not in full
    assert "- references/" not in full
    assert "  - skill-template.md" not in full
    assert "\n- references\n" not in full
    assert "Skill Root:" in full
    assert "Skill Name:" not in full
    assert "Workspace Path:" not in full
    assert "Visibility:" not in full
    assert "Workspace ID:" not in full
    assert "Project ID:" not in full
    assert "Instruction Path:" not in full
    assert "References Dir:" not in full
    assert "Scripts Dir:" not in full
    assert "Assets Dir:" not in full
    assert "Templates Dir:" not in full
    assert "Examples Dir:" not in full
    assert '"skillRoot"' not in full
    assert "按当前 skill 的要求去做。" not in full

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

    with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
        script = fetch_skill_instructions.invoke(
            {
                "skill_name": skill_name,
                "relative_path": "scripts/check-quality.py",
            }
        )
    assert "=== SKILL FILE ===" in script
    assert "Relative Path: scripts/check-quality.py" in script
    assert "Execution Boundary:" in script
    assert 'mode="run_script"' in script
    assert "print('ok:'" in script

    # Large scripts do not need to be read in full before a governed run. The
    # selected SKILL.md contract is the authority for whether the script is used.
    with patch(
        "core.workspace_capability.workspace_authority_service.resolve_from_context",
        return_value=_trusted_workspace_authority(workspace),
    ), bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
        script_result = fetch_skill_instructions.invoke(
            {
                "skill_name": skill_name,
                "mode": "run_script",
                "relative_path": "scripts/check-quality.py",
                "script_args": ["alpha", "beta"],
            }
        )
    assert "=== SKILL SCRIPT RESULT ===" in script_result
    assert "Status: completed" in script_result
    assert "Script: scripts/check-quality.py" in script_result
    assert "ok:alpha|beta" in script_result
    assert str(skill_root) not in script_result

    with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
        non_script_result = fetch_skill_instructions.invoke(
            {
                "skill_name": skill_name,
                "mode": "run_script",
                "relative_path": "references/skill-template.md",
            }
        )
    assert "Status: failed" in non_script_result
    assert "only accepts" in non_script_result

    with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
        escaped = fetch_skill_instructions.invoke(
            {
                "skill_name": skill_name,
                "relative_path": "../SKILL.md",
            }
        )
    assert "=== SKILL FILE ERROR ===" in escaped
    assert "relative_path must stay inside the skill directory" in escaped


def test_fetch_skill_instructions_list_mode_returns_clean_catalog():
    inventory = {
        "items": [
            {
                "skillId": "global:huashu-nuwa",
                "skillName": "huashu-nuwa",
                "name": "huashu-nuwa",
                "folder": "huashu-nuwa",
                "description": "深度调研并生成可运行的人物 Skill。",
                "sourceType": "global",
                "visibility": "global",
                "skillRoot": r"C:\Users\sunny\.agents\skills\huashu-nuwa",
            },
            {
                "skillId": "scoped:test2-demo",
                "skillName": "test2-demo",
                "name": "test2-demo",
                "folder": "test2-demo",
                "description": "当前工作区的演示技能。",
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspaceId": "test2",
                "skillRoot": r"E:\Projects\test2\.agents\skills\test2-demo",
            },
        ],
        "rootDescriptors": [
            {
                "sourceType": "scoped_workspace",
                "visibility": "scoped",
                "workspaceId": "test2",
                "workspacePath": r"E:\Projects\test2",
                "rootPath": r"E:\Projects\test2\.agents\skills",
            }
        ],
    }

    with patch.object(SkillLoader, "get_inventory", return_value=inventory):
        output = fetch_skill_instructions.invoke({"mode": "list"})

    assert output == (
        "global:\n"
        "huashu-nuwa\n"
        "- 深度调研并生成可运行的人物 Skill。\n"
        "scope: test2\n"
        "test2-demo\n"
        "- 当前工作区的演示技能。"
    )
    assert "Skill ID" not in output
    assert "skillRoot" not in output
    assert "C:\\" not in output
    assert "E:\\" not in output


def test_run_skill_script_allows_selected_global_skill_but_blocks_unrelated_external_input(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    global_skill_root = tmp_path / "global-skills" / "external-script-skill"
    scripts = global_skill_root / "scripts"
    scripts.mkdir(parents=True)
    instruction_path = global_skill_root / "SKILL.md"
    instruction_path.write_text(
        "---\nname: external-script-skill\ndescription: Run a governed helper.\n---\n\n"
        "# External Script Skill\n\nRun `scripts/report.py` when a report is needed.\n",
        encoding="utf-8",
    )
    (scripts / "report.py").write_text(
        "import sys\nprint('report:' + '|'.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    unrelated = tmp_path / "outside-secret.txt"
    unrelated.write_text("not available to the skill command", encoding="utf-8")
    skill = {
        "skillId": "global:external-script-skill",
        "skillName": "external-script-skill",
        "name": "external-script-skill",
        "path": str(global_skill_root),
        "skillRoot": str(global_skill_root),
        "instructionPath": str(instruction_path),
        "instructions": instruction_path.read_text(encoding="utf-8"),
        "availableFiles": ["scripts/report.py"],
        "sourceType": "global",
    }

    with patch.object(SkillLoader, "resolve_skill_matches", return_value=[skill]), patch(
        "core.workspace_capability.workspace_authority_service.resolve_from_context",
        return_value=_trusted_workspace_authority(workspace),
    ):
        with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
            success = fetch_skill_instructions.invoke(
                {
                    "skill_name": "external-script-skill",
                    "mode": "run_script",
                    "relative_path": "scripts/report.py",
                    "script_args": ["ok"],
                }
            )
        with bind_runtime_context(workspace_path=str(workspace), runtime_kind="chat"):
            blocked = fetch_skill_instructions.invoke(
                {
                    "skill_name": "external-script-skill",
                    "mode": "run_script",
                    "relative_path": "scripts/report.py",
                    "script_args": [str(unrelated)],
                }
            )

    assert "Status: completed" in success
    assert "report:ok" in success
    assert str(global_skill_root) not in success
    assert "Status: failed" in blocked
    assert "Active Workspace Root" in blocked or "工作区" in blocked
    assert str(unrelated) not in blocked
