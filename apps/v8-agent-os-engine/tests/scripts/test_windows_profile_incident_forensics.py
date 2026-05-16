from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import windows_profile_incident_forensics as forensics  # noqa: E402


class WindowsProfileIncidentForensicsTests(unittest.TestCase):
    def test_parse_timestamp_normalizes_to_utc(self):
        parsed = forensics.parse_timestamp("2026-05-03T10:00:00+08:00")
        self.assertEqual(parsed, datetime(2026, 5, 3, 2, 0, tzinfo=timezone.utc))

    def test_collect_v8_sqlite_evidence_filters_risk_rows(self):
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "v8chat.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE runtime_events (created_at TEXT, event_type TEXT, payload TEXT)")
            conn.execute(
                "INSERT INTO runtime_events VALUES (?, ?, ?)",
                ("2026-05-03T01:00:00+00:00", "tool", json.dumps({"command": "icacls NTUSER.DAT"})),
            )
            conn.execute(
                "INSERT INTO runtime_events VALUES (?, ?, ?)",
                ("2026-05-03T01:00:00+00:00", "tool", json.dumps({"command": "pytest"})),
            )
            conn.commit()
            conn.close()

            evidence = forensics.collect_v8_sqlite_evidence(
                Path(temp_dir),
                datetime(2026, 5, 3, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 5, 3, 2, 0, tzinfo=timezone.utc),
            )

        tables = evidence["databases"][0]["tables"]
        self.assertEqual(tables[0]["table"], "runtime_events")
        self.assertEqual(len(tables[0]["matchedRows"]), 1)
        self.assertIn("NTUSER.DAT", tables[0]["matchedRows"][0]["payload"])

    def test_build_markdown_report_marks_unavailable_sources(self):
        report = {
            "generatedAt": "2026-05-03T00:00:00+00:00",
            "window": {"start": None, "end": None},
            "evidenceSources": ["fixture"],
            "v8": {"runtimeRoot": "X", "databases": []},
            "windowsEvents": {"available": False, "error": "denied"},
            "windowsProfileState": {"available": True, "data": {}},
        }
        markdown = forensics.build_markdown_report(report)
        self.assertIn("Windows Profile Incident Forensics", markdown)
        self.assertIn("unavailable", markdown)
        self.assertIn("read-only", markdown)

    def test_build_report_uses_read_only_collectors(self):
        with patch.object(forensics, "collect_v8_sqlite_evidence", return_value={"runtimeRoot": "R", "databases": []}), patch.object(
            forensics,
            "collect_windows_profile_events",
            return_value={"available": False},
        ), patch.object(forensics, "collect_windows_profile_state", return_value={"available": False}):
            report = forensics.build_report(None, None, Path("R"))
        self.assertIn("V8 SQLite", report["evidenceSources"][0])
        self.assertEqual(report["v8"]["runtimeRoot"], "R")


if __name__ == "__main__":
    unittest.main()
