"""Offline tests of the production materialized-index pagination function."""
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[2] / "api" / "session_history_paging.py"
SPEC = importlib.util.spec_from_file_location("session_history_paging", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
page = MODULE.page_session_history


class SessionHistoryPagingTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"generatedAt": "fixture-v1", "sessions": [
            {"id": f"session-{index:05d}", "historySortAt": "2026-09-13T00:00:00Z", "title": f"group-{index % 3}"}
            for index in range(1000)
        ]}

    def test_equal_timestamps_have_no_duplicates_or_gaps(self):
        cursor, ids = "", []
        while True:
            result = page(self.payload, authority="A", principal="Owner", cursor=cursor, limit=37)
            self.assertLessEqual(len(result["sessions"]), 37)
            ids.extend(row["id"] for row in result["sessions"])
            cursor = result["pageInfo"]["nextCursor"]
            if not cursor:
                break
        self.assertEqual(len(ids), 1000)
        self.assertEqual(len(set(ids)), 1000)
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_cursor_cannot_cross_authority_principal_filter_or_snapshot(self):
        cursor = page(self.payload, authority="A", principal="Owner", limit=2)["pageInfo"]["nextCursor"]
        for overrides in ({"authority": "B"}, {"principal": "owner"}, {"query": "group-2"}):
            with self.assertRaises(MODULE.SessionHistoryCursorError):
                page(self.payload, **{"authority": "A", "principal": "Owner", "cursor": cursor, **overrides})
        with self.assertRaises(MODULE.SessionHistoryCursorError):
            page({**self.payload, "generatedAt": "fixture-v2"}, authority="A", principal="Owner", cursor=cursor)

    def test_filter_is_applied_before_page_limit_and_cannot_reveal_another_group(self):
        result = page(self.payload, authority="A", principal="Owner", query=" GROUP-2 ", limit=25)
        self.assertEqual(len(result["sessions"]), 25)
        self.assertTrue(all(row["title"] == "group-2" for row in result["sessions"]))
        next_page = page(self.payload, authority="A", principal="Owner", query="group-2", cursor=result["pageInfo"]["nextCursor"], limit=25)
        self.assertFalse({row["id"] for row in result["sessions"]} & {row["id"] for row in next_page["sessions"]})

    def test_invalid_cursor_does_not_fallback_to_a_full_unscoped_list(self):
        for cursor in ("x", "W10", "x" * 4097):
            with self.assertRaises(MODULE.SessionHistoryCursorError):
                page(self.payload, authority="A", principal="Owner", cursor=cursor)
        with self.assertRaises(MODULE.SessionHistoryCursorError):
            page(self.payload, authority="", principal="Owner")


if __name__ == "__main__":
    unittest.main()
