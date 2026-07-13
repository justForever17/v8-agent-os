from __future__ import annotations

import json
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

    def test_add_knowledge_clears_orphan_fts_row_before_insert(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            with db._conn() as conn:
                conn.execute(
                    "INSERT INTO knowledge_fts(rowid, fact_tokenized, category, scope) VALUES (?, ?, ?, ?)",
                    (1, "orphan stale row", "general", "global"),
                )

            db.add_knowledge("fact-orphan-rowid", "fresh indexed fact", scope="global")
            matches = db.fts_search("fresh", scope="global")

        self.assertEqual([item["id"] for item in matches], ["fact-orphan-rowid"])

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

    def test_maintenance_compaction_supersedes_only_same_scope_duplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            fact = "V8OS Memory Maintenance v2 uses hierarchical weekly monthly yearly summaries"
            db.add_knowledge("same-scope-old", fact, scope="project:test7", category="memory", confidence=0.5)
            db.add_knowledge("same-scope-new", fact, scope="project:test7", category="memory", confidence=0.9)
            db.add_knowledge("other-scope", fact, scope="project:test1", category="memory", confidence=0.95)
            db.add_entity("Memory Maintenance v2")
            db.add_relation("Memory Maintenance v2", "USES", "hierarchical summaries", source_fact_id="same-scope-old")

            result = db.maintenance_compact_knowledge(limit=20, auto_supersede_threshold=0.99, max_clusters=10)
            project_test7 = {item["id"]: item for item in db.get_all_knowledge(scope="project:test7", limit=10)}
            project_test1 = {item["id"]: item for item in db.get_all_knowledge(scope="project:test1", limit=10)}
            with db._conn() as conn:
                superseded = conn.execute(
                    "SELECT lifecycle_state, superseded_by FROM knowledge WHERE id = ?",
                    ("same-scope-old",),
                ).fetchone()
                relation = conn.execute("SELECT source_fact_id FROM relations WHERE subject = ?", ("memory maintenance v2",)).fetchone()
            active_count = db.get_knowledge_count()

        self.assertEqual(result["supersededCount"], 1)
        self.assertEqual(set(project_test7), {"same-scope-new"})
        self.assertEqual(superseded["lifecycle_state"], "superseded")
        self.assertEqual(superseded["superseded_by"], "same-scope-new")
        self.assertEqual(project_test1["other-scope"]["lifecycle_state"], "active")
        self.assertEqual(active_count, 2)
        self.assertIsNotNone(relation)
        self.assertEqual(relation["source_fact_id"], "same-scope-old")
        self.assertEqual(result["graph"]["rewiredRelationCount"], 0)

    def test_graph_maintenance_prunes_only_old_runtime_owned_isolated_entities(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            db.add_entity("runtime old", maintainer_source="memory_runtime")
            db.add_entity("runtime recent", maintainer_source="memory_runtime")
            db.add_entity("human old", maintainer_source="human_admin")
            with db._conn() as conn:
                conn.execute(
                    "UPDATE entities SET created_at = ?, updated_at = ? WHERE name IN (?, ?)",
                    ("2020-01-01 00:00:00", "2020-01-01 00:00:00", "runtime old", "human old"),
                )

            result = db.maintenance_compact_graph(limit=20, isolated_entity_grace_days=7)
            with db._conn() as conn:
                remaining = {row["name"] for row in conn.execute("SELECT name FROM entities")}

        self.assertEqual(result["isolatedEntityCountBefore"], 3)
        self.assertEqual(result["prunedIsolatedEntityCount"], 1)
        self.assertEqual(result["isolatedEntityCount"], 2)
        self.assertEqual(remaining, {"runtime recent", "human old"})

    def test_maintenance_compaction_records_uncertain_merge_suggestion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = KnowledgeDB(Path(temp_dir) / "knowledge.db")
            db.add_knowledge(
                "candidate-old",
                "V8OS memory runtime keeps project scoped engineering repair evidence packs",
                scope="project:test7",
                category="memory",
                confidence=0.5,
            )
            db.add_knowledge(
                "candidate-new",
                "V8OS memory runtime keeps project scoped engineering repair evidence pack",
                scope="project:test7",
                category="memory",
                confidence=0.9,
            )

            result = db.maintenance_compact_knowledge(limit=20, auto_supersede_threshold=0.999, max_clusters=10)
            with db._conn() as conn:
                old_row = conn.execute("SELECT lifecycle_state, metadata_json FROM knowledge WHERE id = ?", ("candidate-old",)).fetchone()
                new_row = conn.execute("SELECT lifecycle_state, metadata_json FROM knowledge WHERE id = ?", ("candidate-new",)).fetchone()
            old_meta = json.loads(old_row["metadata_json"] or "{}")

        self.assertEqual(result["supersededCount"], 0)
        self.assertEqual(result["mergeSuggestionCount"], 1)
        self.assertEqual(old_row["lifecycle_state"], "active")
        self.assertEqual(new_row["lifecycle_state"], "active")
        self.assertEqual(old_meta["maintenance"]["mergeSuggestion"]["targetId"], "candidate-new")

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

