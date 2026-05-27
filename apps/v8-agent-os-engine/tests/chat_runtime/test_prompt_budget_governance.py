from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException



from api.config_registry_routes import _save_supervisor_domain  # noqa: E402
from core.prompt_budget import estimate_prompt_tokens, truncate_to_estimated_tokens  # noqa: E402
from graph.supervisor_context import _build_workspace_rules_context, workspace_resolution_service  # noqa: E402
from runtimes.extensions.skills.loader import SkillLoader  # noqa: E402


class PromptBudgetGovernanceTests(unittest.TestCase):
    def test_estimator_and_truncation_are_conservative(self):
        text = "hello world " * 200 + "中文" * 200
        self.assertGreater(estimate_prompt_tokens(text), 0)
        clipped = truncate_to_estimated_tokens(text, 100)
        self.assertLessEqual(estimate_prompt_tokens(clipped), 100)
        self.assertLess(len(clipped), len(text))

    def test_supervisor_prompt_save_rejects_over_budget_prompt(self):
        with self.assertRaises(HTTPException) as raised:
            _save_supervisor_domain({"data": {"systemPrompt": "治理" * 10_050}})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("V8_AGENT_OS.md", str(raised.exception.detail))

    def test_workspace_rules_only_reads_agents_md_in_single_default_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "main"
            rules = workspace / ".agents" / "rules"
            rules.mkdir(parents=True)
            (rules / "AGENTS.md").write_text("main agents rules", encoding="utf-8")
            (rules / "OTHER.md").write_text("must not be injected", encoding="utf-8")

            descriptor = {
                "workspaceRoot": str(workspace),
                "mainWorkspacePath": str(workspace),
                "isScopedOverride": False,
            }
            with patch.object(workspace_resolution_service, "get_main_workspace_path", return_value=str(workspace)), patch.object(
                workspace_resolution_service,
                "resolve_workspace_descriptor",
                return_value=descriptor,
            ):
                content, diagnostics = _build_workspace_rules_context(state={}, session_id="s1")

        self.assertIn("main agents rules", content)
        self.assertNotIn("must not be injected", content)
        self.assertEqual(len(diagnostics), 1)

    def test_workspace_rules_and_skills_use_global_plus_one_current_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            main = Path(temp_dir) / "main"
            scoped = Path(temp_dir) / "project"
            for root in (main, scoped):
                (root / ".agents" / "rules").mkdir(parents=True)
                (root / ".agents" / "skills").mkdir(parents=True)
            (main / ".agents" / "rules" / "AGENTS.md").write_text("main rules", encoding="utf-8")
            (scoped / ".agents" / "rules" / "AGENTS.md").write_text("scoped rules", encoding="utf-8")
            global_skills_root = Path(temp_dir) / "global" / "skills"
            global_skill = global_skills_root / "global-only"
            main_skill = main / ".agents" / "skills" / "main-only"
            scoped_skill = scoped / ".agents" / "skills" / "scoped-only"
            for skill_root in (global_skill, main_skill, scoped_skill):
                skill_root.mkdir(parents=True)
                (skill_root / "SKILL.md").write_text(
                    f"# {skill_root.name}\n\nUse this skill for {skill_root.name}.",
                    encoding="utf-8",
                )

            descriptor = {
                "workspaceRoot": str(scoped),
                "mainWorkspacePath": str(main),
                "isScopedOverride": True,
                "workspaceId": "project-workspace",
                "projectId": "project-1",
            }
            with patch.object(workspace_resolution_service, "get_main_workspace_path", return_value=str(main)), patch.object(
                workspace_resolution_service,
                "resolve_workspace_descriptor",
                return_value=descriptor,
            ):
                content, _diagnostics = _build_workspace_rules_context(
                    state={"workspace_id": "project-workspace"},
                    session_id="s1",
                )
                skill_roots = SkillLoader._resolve_root_descriptors(
                    include_scoped=True,
                    session_id="s1",
                    explicit_workspace_id="project-workspace",
                    runtime_kind="chat",
                )

            global_desc = {"sourceType": "global", "rootPath": str(global_skills_root)}
            main_desc = {"sourceType": "main_workspace", "rootPath": str(main / ".agents" / "skills")}
            scoped_desc = {"sourceType": "scoped_workspace", "rootPath": str(scoped / ".agents" / "skills")}
            old_state = {
                "_skills_registry": SkillLoader._skills_registry,
                "_skills_root_descriptors": SkillLoader._skills_root_descriptors,
                "_skills_fingerprint": SkillLoader._skills_fingerprint,
                "_skills_revision": SkillLoader._skills_revision,
                "_root_inventory_states": SkillLoader._root_inventory_states,
                "_visible_inventory_cache": SkillLoader._visible_inventory_cache,
            }
            try:
                SkillLoader._skills_registry = {
                    "global:1": {
                        "skillId": "global:1",
                        "skillName": "global-only",
                        "rootPath": str(global_skill),
                        "instructionPath": str(global_skill / "SKILL.md"),
                        "sourceType": "global",
                    },
                    "main:1": {
                        "skillId": "main:1",
                        "skillName": "main-only",
                        "rootPath": str(main_skill),
                        "instructionPath": str(main_skill / "SKILL.md"),
                        "sourceType": "main_workspace",
                    },
                }
                SkillLoader._skills_root_descriptors = [global_desc, main_desc]
                SkillLoader._skills_fingerprint = "base"
                SkillLoader._skills_revision = "base-revision"
                SkillLoader._visible_inventory_cache = {}
                SkillLoader._root_inventory_states = {
                    str(global_skills_root): {
                        "rootPath": str(global_skills_root),
                        "descriptor": dict(global_desc),
                        "descriptorSignature": "global",
                        "rootRevision": "global-revision",
                        "manifest": {},
                        "registry": {
                            "global:1": {
                                "skillId": "global:1",
                                "skillName": "global-only",
                                "rootPath": str(global_skill),
                                "instructionPath": str(global_skill / "SKILL.md"),
                                "sourceType": "global",
                            }
                        },
                        "lastScanAt": "2026-04-24T00:00:00Z",
                        "dirty": False,
                    },
                    str(main / ".agents" / "skills"): {
                        "rootPath": str(main / ".agents" / "skills"),
                        "descriptor": dict(main_desc),
                        "descriptorSignature": "main",
                        "rootRevision": "main-revision",
                        "manifest": {},
                        "registry": {
                            "main:1": {
                                "skillId": "main:1",
                                "skillName": "main-only",
                                "rootPath": str(main_skill),
                                "instructionPath": str(main_skill / "SKILL.md"),
                                "sourceType": "main_workspace",
                            }
                        },
                        "lastScanAt": "2026-04-24T00:00:00Z",
                        "dirty": False,
                    },
                }

                def fake_resolve_descriptors(*, include_scoped=False, **_kwargs):  # noqa: ANN003
                    return [global_desc, scoped_desc] if include_scoped else [global_desc, main_desc]

                def fake_scan(descriptors):  # noqa: ANN001, ANN201
                    if any(item.get("sourceType") == "scoped_workspace" for item in descriptors):
                        return {
                            "scoped:1": {
                                "skillId": "scoped:1",
                                "skillName": "scoped-only",
                                "rootPath": str(scoped_skill),
                                "instructionPath": str(scoped_skill / "SKILL.md"),
                                "sourceType": "scoped_workspace",
                            }
                        }
                    return {}

                def fake_scan_single(descriptor):  # noqa: ANN001, ANN201
                    if descriptor.get("sourceType") == "scoped_workspace":
                        return fake_scan([descriptor])
                    return {}

                with patch.object(SkillLoader, "_resolve_root_descriptors", side_effect=fake_resolve_descriptors), patch.object(
                    SkillLoader,
                    "_scan_root_descriptors",
                    side_effect=fake_scan,
                ), patch.object(SkillLoader, "_scan_single_root_descriptor", side_effect=fake_scan_single):
                    inventory = SkillLoader.get_inventory(
                        force_refresh=False,
                        include_scoped=True,
                        explicit_workspace_id="project-workspace",
                        runtime_kind="chat",
                    )
            finally:
                SkillLoader._skills_registry = old_state["_skills_registry"]
                SkillLoader._skills_root_descriptors = old_state["_skills_root_descriptors"]
                SkillLoader._skills_fingerprint = old_state["_skills_fingerprint"]
                SkillLoader._skills_revision = old_state["_skills_revision"]
                SkillLoader._root_inventory_states = old_state["_root_inventory_states"]
                SkillLoader._visible_inventory_cache = old_state["_visible_inventory_cache"]

        self.assertIn("scoped rules", content)
        self.assertNotIn("main rules", content)
        sources = [item.get("sourceType") for item in skill_roots]
        self.assertIn("global", sources)
        self.assertIn("scoped_workspace", sources)
        self.assertNotIn("main_workspace", sources)
        inventory_names = [item.get("skillName") for item in inventory.get("items", [])]
        self.assertIn("global-only", inventory_names)
        self.assertIn("scoped-only", inventory_names)
        self.assertNotIn("main-only", inventory_names)

    def test_workspace_less_network_api_omits_default_workspace_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "main"
            rules = workspace / ".agents" / "rules"
            rules.mkdir(parents=True)
            (rules / "AGENTS.md").write_text("main agents rules", encoding="utf-8")

            descriptor = {
                "workspaceRoot": str(workspace),
                "mainWorkspacePath": str(workspace),
                "isScopedOverride": False,
            }
            with patch.object(workspace_resolution_service, "get_main_workspace_path", return_value=str(workspace)), patch.object(
                workspace_resolution_service,
                "resolve_workspace_descriptor",
                return_value=descriptor,
            ):
                content, diagnostics = _build_workspace_rules_context(
                    state={"transport": "network_supervisor_openai"},
                    session_id="network-session",
                )

        self.assertEqual(content, "")
        self.assertEqual(diagnostics, [])

    def test_network_api_with_explicit_project_workspace_still_injects_scoped_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            main = Path(temp_dir) / "main"
            scoped = Path(temp_dir) / "project"
            (main / ".agents" / "rules").mkdir(parents=True)
            (scoped / ".agents" / "rules").mkdir(parents=True)
            (main / ".agents" / "rules" / "AGENTS.md").write_text("main rules", encoding="utf-8")
            (scoped / ".agents" / "rules" / "AGENTS.md").write_text("scoped rules", encoding="utf-8")

            descriptor = {
                "workspaceRoot": str(scoped),
                "mainWorkspacePath": str(main),
                "isScopedOverride": True,
                "workspaceId": "project-workspace",
                "projectId": "project-1",
            }
            with patch.object(workspace_resolution_service, "get_main_workspace_path", return_value=str(main)), patch.object(
                workspace_resolution_service,
                "resolve_workspace_descriptor",
                return_value=descriptor,
            ):
                content, diagnostics = _build_workspace_rules_context(
                    state={
                        "transport": "network_supervisor_openai",
                        "workspace_id": "project-workspace",
                        "project_id": "project-1",
                    },
                    session_id="network-session",
                )

        self.assertIn("scoped rules", content)
        self.assertNotIn("main rules", content)
        self.assertEqual(len(diagnostics), 1)


if __name__ == "__main__":
    unittest.main()

