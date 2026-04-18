from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.memory_store as memory_store_module
from core.memory_store import MemoryStore
from core.storage import storage


def _write_day_log(path: Path, *, summaries: list[str] | None = None, scope: str = "global") -> None:
    summaries = summaries or []
    lines = [
        "---",
        "summaries:",
        *[f'  - "{item}"' for item in summaries],
        "---",
        "",
        "### 10:00",
        f"effective_memory_scope: {scope}",
        "summary: test entry",
        "",
        "body",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


class MemoryBrokeredMapTests(unittest.TestCase):
    def test_memory_map_uses_virtual_refs_and_tracks_missing_or_stale_summaries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "MEMORY_ROOT", memory_root):
                store = MemoryStore()
                month_dir = memory_root / "daily" / "2026" / "04_april"
                week_dir = month_dir / "week_16"
                day17 = week_dir / "2026-04-17.md"
                day18 = week_dir / "2026-04-18.md"
                _write_day_log(day17, summaries=["完成了工作流调试"])
                _write_day_log(day18, summaries=["补齐了记忆摘要"])

                week_summary = week_dir / "summary.md"
                week_summary.parent.mkdir(parents=True, exist_ok=True)
                week_summary.write_text("weekly summary", encoding="utf-8")
                os.utime(week_summary, (1, 1))
                os.utime(day17, (2, 2))
                os.utime(day18, (3, 3))

                memory_map = store.build_memory_map(anchor_date="2026-04-18")
                self.assertEqual(memory_map["currentRefs"]["day"], "memory://day/2026-04-18")
                self.assertEqual(memory_map["items"][0]["memoryRef"], "memory://year/2026")
                self.assertEqual(memory_map["items"][0]["summaryState"], "missing")

                expanded_year = store.expand_memory_map("memory://year/2026")
                self.assertEqual(expanded_year["children"][0]["memoryRef"], "memory://month/2026-04")

                expanded_month = store.expand_memory_map("memory://month/2026-04")
                self.assertEqual(expanded_month["children"][0]["memoryRef"], "memory://week/2026-W16")
                self.assertEqual(expanded_month["children"][0]["summaryState"], "stale")

                stale_targets = store.list_summary_targets(states=["stale"])
                self.assertEqual([item["memoryRef"] for item in stale_targets], ["memory://week/2026-W16"])

                health = store.get_memory_map_health()
                self.assertEqual(health["counts"]["missing"], 2)
                self.assertEqual(health["counts"]["stale"], 1)
                self.assertIn("memory://year/2026", health["missingRefs"])
                self.assertIn("memory://month/2026-04", health["missingRefs"])
                self.assertIn("memory://week/2026-W16", health["staleRefs"])

                day_content = store.read_memory_day("memory://day/2026-04-17")
                self.assertIn("Ref: memory://day/2026-04-17", day_content)
                self.assertNotIn(str(memory_root), day_content)

                with patch.object(storage, "get_memory_config", return_value={"max_recent_days": 1, "max_context_tokens": 4000}):
                    context = store.build_session_context(user_query="总结一下最近记忆")
                self.assertIn("Ref: memory://day/2026-04-18", context)
                self.assertIn("Ref: memory://week/2026-W16", context)
                self.assertNotIn(str(memory_root), context)


if __name__ == "__main__":
    unittest.main()
