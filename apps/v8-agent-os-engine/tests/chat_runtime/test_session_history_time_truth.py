from __future__ import annotations

import gc
import shutil
import tempfile
import unittest
from pathlib import Path

from core.database import DatabaseManager
from core.time_truth import normalize_utc_iso
from erc.session_history_contract import build_session_history_materialized_record


class SessionHistoryTimeTruthTests(unittest.TestCase):
    def test_normalize_utc_iso_converts_sqlite_naive_string(self):
        self.assertEqual(
            normalize_utc_iso("2026-04-17 16:39:10"),
            "2026-04-17T16:39:10.000Z",
        )

    def test_get_sessions_sorts_by_history_sort_at_not_runtime_activity(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            db = DatabaseManager(temp_dir / "state.db")
            db.create_or_update_session("sess_a", "A", user_id="user")
            db.create_or_update_session("sess_b", "B", user_id="user")
            db.create_run_record("run_a", "sess_a", user_id="user")
            db.add_message("msg_b", "sess_b", "user", "hello")
            db.add_runtime_event(
                {
                    "event_id": "evt_a",
                    "session_id": "sess_a",
                    "run_id": "run_a",
                    "seq": 1,
                    "kind": "event",
                    "topic": "runtime.progress",
                    "ts": "2026-04-18T00:00:10.000Z",
                    "payload": {"label": "runtime only"},
                }
            )

            with db.get_connection() as conn:
                conn.execute(
                    "UPDATE sessions SET created_at = ?, updated_at = ? WHERE id = ?",
                    ("2026-04-18T00:00:00.000Z", "2026-04-18T00:00:00.000Z", "sess_a"),
                )
                conn.execute(
                    "UPDATE sessions SET created_at = ?, updated_at = ? WHERE id = ?",
                    ("2026-04-18T00:00:00.000Z", "2026-04-18T00:00:00.000Z", "sess_b"),
                )
                conn.execute(
                    "UPDATE messages SET created_at = ? WHERE id = ?",
                    ("2026-04-18T00:00:05.000Z", "msg_b"),
                )
                conn.commit()

            sessions = db.get_sessions()
            self.assertGreaterEqual(len(sessions), 2)
            ordered_ids = [item["id"] for item in sessions[:2]]
            self.assertEqual(ordered_ids, ["sess_b", "sess_a"])

            by_id = {item["id"]: item for item in sessions}
            self.assertEqual(by_id["sess_b"]["historySortAt"], "2026-04-18T00:00:05.000Z")
            self.assertEqual(by_id["sess_a"]["historySortAt"], "2026-04-18T00:00:00.000Z")
            self.assertEqual(by_id["sess_a"]["lastActivityAt"], "2026-04-18T00:00:10.000Z")
        finally:
            del db
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_sessions_hides_prompt_cache_live_audit_sessions(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            db = DatabaseManager(temp_dir / "state.db")
            db.create_or_update_session(
                "sess_planner_live",
                "Planner Prompt Cache Live Audit",
                user_id="prompt_cache_live",
                agent_id="planner_prompt_cache_live",
                metadata={"source": "planner_prompt_cache_live"},
            )
            db.add_message("msg_planner_live", "sess_planner_live", "user", "diagnostic run")

            sessions = db.get_sessions()

            self.assertNotIn("sess_planner_live", {item["id"] for item in sessions})
        finally:
            del db
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_sessions_repairs_planner_runtime_title_from_latest_user_message(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            db = DatabaseManager(temp_dir / "state.db")
            db.create_or_update_session("sess_planner_title", "Planner · route draft", user_id="user")
            db.add_message("msg_planner_title", "sess_planner_title", "user", "请整理 DeepSeek prompt cache 命中率")

            sessions = db.get_sessions()
            by_id = {item["id"]: item for item in sessions}

            self.assertEqual(by_id["sess_planner_title"]["title"], "请整理 DeepSeek prompt cache 命中率")
        finally:
            del db
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_internal_runtime_title_does_not_overwrite_existing_user_title(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            db = DatabaseManager(temp_dir / "state.db")
            db.create_or_update_session("sess_existing_title", "用户标题", user_id="user")
            db.create_or_update_session("sess_existing_title", "Planner · route draft", user_id="user")

            sessions = db.get_sessions()
            by_id = {item["id"]: item for item in sessions}

            self.assertEqual(by_id["sess_existing_title"]["title"], "用户标题")
        finally:
            del db
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_manual_session_title_and_pin_survive_runtime_updates(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            db = DatabaseManager(temp_dir / "state.db")
            db.create_or_update_session("sess_manual_title", "自动标题", user_id="user")
            updated = db.update_session_presentation(
                "sess_manual_title",
                {"title": "用户命名的任务", "pinned": True},
            )
            self.assertIsNotNone(updated)

            db.create_or_update_session("sess_manual_title", "后续消息生成的标题", user_id="user")
            session = db.get_session("sess_manual_title")

            self.assertEqual(session["title"], "用户命名的任务")
            self.assertTrue(session["metadata"]["manualTitle"])
            self.assertTrue(session["metadata"]["pinned"])
            self.assertTrue(session["metadata"]["pinnedAt"])

            db.update_session_presentation("sess_manual_title", {"pinned": False})
            session = db.get_session("sess_manual_title")
            self.assertNotIn("pinned", session["metadata"])
            self.assertNotIn("pinnedAt", session["metadata"])
        finally:
            del db
            gc.collect()
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_materialized_record_keeps_history_sort_at_stable_when_runtime_is_newer(self):
        record = build_session_history_materialized_record(
            session_row={
                "id": "sess_demo",
                "title": "Demo",
                "metadata": "{}",
                "created_at": "2026-04-18 00:00:00",
                "updated_at": "2026-04-18 00:00:00",
                "latestMessageAt": "2026-04-18 00:00:05",
                "latestRuntimeEventAt": "2026-04-18 00:00:10",
            },
            workflow_view={},
            approvals=[],
            snapshot=None,
            latest_seq=10,
            source="runtime_snapshot",
        )

        self.assertEqual(record["historySortAt"], "2026-04-18T00:00:05.000Z")
        self.assertEqual(record["lastActivityAt"], "2026-04-18T00:00:10.000Z")
        self.assertEqual(record["createdAt"], "2026-04-18T00:00:00.000Z")


if __name__ == "__main__":
    unittest.main()
