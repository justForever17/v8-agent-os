from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path



from core.knowledge_db import KnowledgeDB  # noqa: E402
from graph.supervisor_context import _build_memory_recall_block  # noqa: E402


class MemoryLifecycleTests(unittest.TestCase):
    def test_schema_migration_is_idempotent_and_adds_lifecycle_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "knowledge.db"
            KnowledgeDB(db_path)
            db = KnowledgeDB(db_path)
            with db._conn() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge)").fetchall()}

        self.assertIn("lifecycle_state", columns)
        self.assertIn("agents_hash", columns)
        self.assertIn("repo_signature", columns)
        self.assertIn("maintainer_source", columns)
        self.assertIn("effective_confidence", columns)

    def test_stale_revalidation_blocks_search_until_human_revalidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            db.add_knowledge(
                "fact-stale",
                "alpha runtime memory",
                scope="project:demo",
                agents_hash="agents-old",
                repo_signature="repo-old",
                confidence=0.6,
            )

            marked = db.mark_stale_for_signature_mismatch(
                scopes=["project:demo"],
                agents_hash="agents-new",
                repo_signature="repo-new",
            )
            hidden = db.fts_search("alpha", scope="project:demo")

            revalidated = db.revalidate_knowledge(
                "fact-stale",
                agents_hash="agents-new",
                repo_signature="repo-new",
                maintainer_source="human_admin",
            )
            visible = db.fts_search("alpha", scope="project:demo")
            current = db.get_all_knowledge(scope="project:demo", limit=10)[0]

        self.assertEqual(marked, 1)
        self.assertEqual(hidden, [])
        self.assertTrue(revalidated)
        self.assertEqual(visible[0]["id"], "fact-stale")
        self.assertEqual(current["lifecycle_state"], "active")
        self.assertEqual(current["maintainer_source"], "human_admin")
        self.assertAlmostEqual(current["effective_confidence"], 0.9)

    def test_human_admin_confidence_advantage_does_not_unhide_stale_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            db.add_knowledge("runtime", "shared scoring fact", scope="project:demo", confidence=0.6)
            db.add_knowledge(
                "human",
                "shared scoring fact",
                scope="project:demo",
                maintainer_source="human_admin",
                confidence=0.6,
                agents_hash="old",
            )
            db.mark_stale_for_signature_mismatch(scopes=["project:demo"], agents_hash="new")

            listed = db.get_all_knowledge(scope="project:demo", limit=10)
            injected = db.fts_search("shared", scope="project:demo")

        self.assertEqual(listed[0]["id"], "human")
        self.assertEqual(listed[0]["lifecycle_state"], "stale")
        self.assertEqual([item["id"] for item in injected], ["runtime"])

    def test_trusted_document_upload_source_can_be_weighted_without_promoting_plain_imports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            db.add_knowledge(
                "plain-doc",
                "document upload scoring fact",
                scope="global",
                maintainer_source="imported_document",
                confidence=0.6,
            )
            db.add_knowledge(
                "trusted-doc",
                "document upload scoring fact",
                scope="global",
                maintainer_source="human_admin",
                confidence=0.67,
            )

            listed = db.get_all_knowledge(scope="global", limit=10)

        self.assertEqual(listed[0]["id"], "trusted-doc")
        self.assertAlmostEqual(listed[0]["effective_confidence"], 1.0)
        self.assertEqual(listed[1]["id"], "plain-doc")
        self.assertAlmostEqual(listed[1]["effective_confidence"], 0.6)

    def test_passive_memory_recall_block_uses_short_preview_for_large_document_parent(self):
        long_fact = "A" * 1000
        block, facts = _build_memory_recall_block(
            [
                {
                    "id": "parent-doc",
                    "fact": long_fact,
                    "category": "user_document",
                    "scope": "global",
                    "final_relevance_score": 0.91,
                }
            ]
        )

        self.assertIsNotNone(block)
        self.assertLessEqual(len(facts[0]["fact"]), 243)
        self.assertTrue(facts[0]["fact"].endswith("..."))
        self.assertNotIn(long_fact, block["content"])


if __name__ == "__main__":
    unittest.main()

