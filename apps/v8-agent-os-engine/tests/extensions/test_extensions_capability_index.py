from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.database import DatabaseManager
from core.extensions_capability_index import (
    capability_index_path,
    extensions_runtime_cache_path,
    skill_inventory_cache_path,
    write_capability_index,
)
from erc.safety_guardian import safety_guardian
from runtimes.extensions.skills.loader import SkillLoader, fetch_skill_instructions


class ExtensionsCapabilityIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.cache_dir = self.root / "cache" / "extensions"
        self.env_patch = patch.dict(
            "os.environ",
            {"V8_AGENT_OS_EXTENSIONS_CACHE_DIR": str(self.cache_dir)},
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.db = DatabaseManager(self.root / "state.db")
        self.db_patch = patch("erc.safety_guardian.db", self.db)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self._skill_loader_state = {
            name: copy.deepcopy(getattr(SkillLoader, name))
            for name in (
                "_skills_registry",
                "_skills_root_descriptors",
                "_skills_roots",
                "_skills_manifest",
                "_root_inventory_states",
                "_visible_inventory_cache",
                "_recent_skill_discovery",
                "_startup_state",
                "_snapshot_freshness",
                "_last_refresh_at",
                "_last_refresh_error",
                "_skills_fingerprint",
                "_skills_revision",
                "_skills_root_signature",
            )
        }
        self.addCleanup(self._restore_skill_loader_state)

    def _restore_skill_loader_state(self) -> None:
        for name, value in self._skill_loader_state.items():
            setattr(SkillLoader, name, value)

    def _create_review_skill(self, name: str = "review-skill") -> tuple[Path, Path]:
        root = self.root / "skills" / name
        root.mkdir(parents=True, exist_ok=True)
        skill_md = root / "SKILL.md"
        skill_md.write_text(
            f"---\nname: {name}\ndescription: Review skill fixture\n---\nSensitive payload body should stay hidden before approval.\n",
            encoding="utf-8",
        )
        (root / "payload.exe").write_bytes(b"MZ\x00\x00fixture")
        return root, skill_md

    def _create_plain_skill(self, name: str, body: str = "Plain fixture body.") -> tuple[Path, Path]:
        root = self.root / "skills" / name
        root.mkdir(parents=True, exist_ok=True)
        skill_md = root / "SKILL.md"
        skill_md.write_text(
            f"---\nname: {name}\ndescription: {name} fixture\n---\n{body}\n",
            encoding="utf-8",
        )
        return root, skill_md

    def test_cache_paths_are_under_unified_extensions_cache_dir(self) -> None:
        self.assertEqual(skill_inventory_cache_path(), self.cache_dir / "skills_inventory_cache.json")
        self.assertEqual(extensions_runtime_cache_path(), self.cache_dir / "extensions_runtime_cache.json")
        self.assertEqual(capability_index_path(), self.cache_dir / "extensions_capability_index.json")

    def test_capability_index_records_skill_safety_mcp_risk_and_lexicon_snapshot(self) -> None:
        skill_root, skill_md = self._create_review_skill()
        payload = write_capability_index(
            skills=[
                {
                    "skillId": "review-skill",
                    "name": "review-skill",
                    "path": str(skill_root),
                    "skillRoot": str(skill_root),
                    "instructionPath": str(skill_md),
                    "description": "Review fixture",
                    "instructions": skill_md.read_text(encoding="utf-8"),
                    "capabilityTags": {"languageAliases": ["复核技能", "review fixture"]},
                }
            ],
            mcp_servers=[
                {
                    "name": "dangerous_mcp",
                    "transport": "stdio",
                    "target": "node dangerous.js",
                    "tools": [
                        {"name": "http_request", "description": "Send a raw HTTP request to any URL."},
                        {"name": "run_shell", "description": "Execute a shell command."},
                    ],
                }
            ],
            lexicon_state={"signature": "lexicon:test", "locales": ["zh-CN", "en"], "querySynonyms": {"生成": ["create"]}},
            source="test",
        )

        self.assertTrue(capability_index_path().exists())
        disk_payload = json.loads(capability_index_path().read_text(encoding="utf-8"))
        self.assertEqual(disk_payload["summary"]["skillCount"], 1)
        self.assertEqual(disk_payload["summary"]["approvalRequiredSkillCount"], 1)
        self.assertEqual(disk_payload["aliases"]["signature"], "lexicon:test")
        self.assertIn("复核技能", disk_payload["skills"][0]["aliases"])
        self.assertTrue(disk_payload["skills"][0]["safety"]["approvalRequired"])
        self.assertIn("binary_executable", disk_payload["skills"][0]["safety"]["riskCodes"])
        mcp_risks = set(disk_payload["mcp"]["servers"][0]["riskCodes"])
        self.assertIn("raw_http", mcp_risks)
        self.assertIn("shell_or_process", mcp_risks)
        self.assertEqual(payload["summary"], disk_payload["summary"])

    def test_review_skill_is_candidate_visible_but_fetch_requires_approval(self) -> None:
        skills_root = self.root / "skills"
        skill_root, _skill_md = self._create_review_skill()
        descriptor = SkillLoader._build_root_descriptor(
            root_path=skills_root,
            source_type="global",
            visibility="global",
        )
        with patch.object(SkillLoader, "_discovery_root_descriptors", return_value=[descriptor]), patch.object(
            SkillLoader,
            "_resolve_root_descriptors",
            return_value=[descriptor],
        ):
            SkillLoader.reload_skills(root_descriptors=[descriptor])

            inventory = SkillLoader.get_inventory(force_refresh=False, include_scoped=False)
            indexed_skill = next(item for item in list(inventory.get("items") or []) if item.get("name") == "review-skill")
            self.assertEqual(indexed_skill["safety"]["effectiveVerdict"], "review")
            self.assertTrue(indexed_skill["safety"]["approvalRequired"])

            blocked_output = fetch_skill_instructions.invoke({"skill_name": "review-skill"})
            self.assertIn("SKILL APPROVAL REQUIRED", blocked_output)
            self.assertNotIn("=== INSTRUCTIONS ===", blocked_output)
            self.assertNotIn("Sensitive payload body should stay hidden", blocked_output)

            review = safety_guardian.get_skill_safety_review(
                skill_id=str(indexed_skill.get("skillId") or ""),
                skill_name="review-skill",
                skill_root=str(skill_root),
                instruction_path=str(skill_root / "SKILL.md"),
            )
            self.assertIsNotNone(review)
            approved = safety_guardian.approve_skill_safety_review(review["id"])
            self.assertEqual(approved["user_override"], "approved")

            approved_output = fetch_skill_instructions.invoke({"skill_name": "review-skill"})
            self.assertIn("=== INSTRUCTIONS SUMMARY ===", approved_output)
            self.assertIn("Need more detail?", approved_output)
            self.assertIn("Sensitive payload body should stay hidden before approval.", approved_output)

            approved_full_output = fetch_skill_instructions.invoke({"skill_name": "review-skill", "detail_level": "full"})
            self.assertIn("=== INSTRUCTIONS (FULL) ===", approved_full_output)
            self.assertIn("Sensitive payload body should stay hidden before approval.", approved_full_output)

    def test_single_skill_refresh_reuses_unchanged_safety_and_scans_changed_skill_only(self) -> None:
        skills_root = self.root / "skills"
        self._create_plain_skill("alpha-skill")
        beta_root, beta_md = self._create_plain_skill("beta-skill")
        descriptor = SkillLoader._build_root_descriptor(
            root_path=skills_root,
            source_type="global",
            visibility="global",
        )
        with patch.object(SkillLoader, "_discovery_root_descriptors", return_value=[descriptor]), patch.object(
            SkillLoader,
            "_resolve_root_descriptors",
            return_value=[descriptor],
        ):
            SkillLoader.reload_skills(root_descriptors=[descriptor])
            beta_md.write_text(
                "---\nname: beta-skill\ndescription: beta changed fixture\n---\nChanged body.\n",
                encoding="utf-8",
            )
            with patch.object(
                safety_guardian,
                "assess_skill_directory",
                wraps=safety_guardian.assess_skill_directory,
            ) as assess_spy:
                change = SkillLoader.refresh_root_descriptors_if_changed([descriptor])

            self.assertTrue(change["changed"])
            self.assertIn("beta-skill", [item.get("skillName") for item in change.get("recentSkillDiscovery", [])])
            scanned_roots = [call.kwargs.get("skill_root") for call in assess_spy.call_args_list]
            self.assertEqual(scanned_roots, [str(beta_root)])

    def test_skill_refresh_is_read_only_for_skill_files(self) -> None:
        skills_root = self.root / "skills"
        _skill_root, skill_md = self._create_plain_skill("readonly-skill")
        descriptor = SkillLoader._build_root_descriptor(
            root_path=skills_root,
            source_type="global",
            visibility="global",
        )
        before_hash = hashlib.sha1(skill_md.read_bytes()).hexdigest()
        before_stat = skill_md.stat()
        with patch.object(SkillLoader, "_discovery_root_descriptors", return_value=[descriptor]), patch.object(
            SkillLoader,
            "_resolve_root_descriptors",
            return_value=[descriptor],
        ):
            SkillLoader.reload_skills(root_descriptors=[descriptor])
            SkillLoader.refresh_root_descriptors_if_changed([descriptor], compare_existing=False)

        after_stat = skill_md.stat()
        self.assertEqual(hashlib.sha1(skill_md.read_bytes()).hexdigest(), before_hash)
        self.assertEqual(after_stat.st_size, before_stat.st_size)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)

    def test_fetch_fallback_is_rules_only_and_does_not_wake_llm_review(self) -> None:
        skills_root = self.root / "skills"
        self._create_review_skill("fetch-review-skill")
        descriptor = SkillLoader._build_root_descriptor(
            root_path=skills_root,
            source_type="global",
            visibility="global",
        )
        with patch.object(SkillLoader, "_discovery_root_descriptors", return_value=[descriptor]), patch.object(
            SkillLoader,
            "_resolve_root_descriptors",
            return_value=[descriptor],
        ):
            SkillLoader.reload_skills(root_descriptors=[descriptor])
            skill = next(item for item in SkillLoader.get_inventory(force_refresh=False, include_scoped=False)["items"] if item["name"] == "fetch-review-skill")
            review = safety_guardian.get_skill_safety_review(
                skill_id=str(skill.get("skillId") or ""),
                skill_name="fetch-review-skill",
                skill_root=str(skill.get("path") or ""),
                instruction_path=str(skill.get("instructionPath") or ""),
            )
            self.assertIsNotNone(review)
            with self.db.get_connection() as conn:
                conn.execute("DELETE FROM skill_safety_reviews WHERE id = ?", (review["id"],))
                conn.commit()
            with patch.object(safety_guardian, "review_skill_scan_with_llm", side_effect=AssertionError("LLM review should not run")):
                output = fetch_skill_instructions.invoke({"skill_name": "fetch-review-skill"})
            self.assertIn("SKILL APPROVAL REQUIRED", output)

    def test_delete_skill_removes_directory_and_visible_inventory_entry(self) -> None:
        skills_root = self.root / "skills"
        skill_root, _skill_md = self._create_plain_skill("delete-me")
        descriptor = SkillLoader._build_root_descriptor(
            root_path=skills_root,
            source_type="global",
            visibility="global",
        )
        with patch.object(SkillLoader, "_discovery_root_descriptors", return_value=[descriptor]), patch.object(
            SkillLoader,
            "_resolve_root_descriptors",
            return_value=[descriptor],
        ):
            SkillLoader.reload_skills(root_descriptors=[descriptor])
            skill = next(item for item in SkillLoader.get_inventory(force_refresh=False, include_scoped=False)["items"] if item["name"] == "delete-me")
            with patch("runtimes.extensions.skills.loader.Path.home", return_value=self.root):
                result = SkillLoader.delete_skill(str(skill["skillId"]), scope="global")
            self.assertEqual(result["removed"]["skillName"], "delete-me")
            self.assertGreaterEqual(int(result.get("inactiveReviewCount") or 0), 1)
            self.assertTrue(Path(str(result.get("backupPath") or "")).exists())
            self.assertFalse(skill_root.exists())
            inventory = SkillLoader.get_inventory(force_refresh=False, include_scoped=False)
            self.assertFalse(any(item.get("name") == "delete-me" for item in inventory.get("items", [])))


if __name__ == "__main__":
    unittest.main()
