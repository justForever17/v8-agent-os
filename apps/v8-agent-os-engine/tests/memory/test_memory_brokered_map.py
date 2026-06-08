from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import core.memory_store as memory_store_module
from core.memory_store import MemoryStore
from core.storage import storage


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        value = datetime(2026, 4, 18, 12, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


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

                with patch.object(
                    storage,
                    "get_memory_config",
                    return_value={
                        "max_recent_days": 5,
                        "max_context_tokens": 4000,
                        "passive_context_profile": "balanced",
                        "passive_summary_enabled": True,
                        "passive_memory_map_enabled": True,
                        "passive_recent_activity_teaser_enabled": True,
                        "passive_recent_activity_teaser_limit": 2,
                        "passive_memory_map_node_limit": 4,
                    },
                ), patch.object(memory_store_module, "datetime", _FixedDatetime):
                    context = store.build_session_context(user_query="总结一下最近记忆")
                self.assertIn("[MEMORY MAP]", context)
                self.assertIn("[MEMORY SUMMARY]", context)
                self.assertIn("[RECENT ACTIVITY TEASER]", context)
                self.assertIn("call memory_broker", context)
                self.assertIn("Ref: memory://year/2026", context)
                self.assertIn("memory_broker(mode='expand_map'", context)
                self.assertIn("memory_map_expand(memoryRef)", context)
                self.assertIn("Ref: memory://day/2026-04-18", context)
                self.assertIn("Ref: memory://week/2026-W16", context)
                self.assertNotIn("[HIERARCHICAL MEMORY SUMMARIES]", context)
                self.assertNotIn("[PRIOR MEMORY SUMMARY BEFORE DETAILED WINDOW]", context)
                self.assertNotIn("[RECENT ACTIVITY LOGS]", context)
                self.assertNotIn(str(memory_root), context)

    def test_memory_log_and_map_markers_are_supervisor_only_by_default(self):
        store = MemoryStore()
        memory_config = {
            "max_recent_days": 3,
            "max_context_tokens": 4000,
            "passive_context_profile": "balanced",
            "passive_summary_enabled": True,
            "passive_memory_map_enabled": True,
            "passive_recent_activity_teaser_enabled": True,
            "passive_knowledge_graph_summary_enabled": False,
        }

        with (
            patch.object(storage, "get_memory_config", return_value=memory_config),
            patch.object(store, "load_preferences", return_value={}),
            patch.object(store, "_build_memory_summary_for_injection", return_value="daily summary"),
            patch.object(store, "_format_memory_map_for_injection", return_value="memory://day/2026-05-01"),
            patch.object(store, "_build_recent_activity_teaser", return_value="recent activity"),
            patch.object(store, "_build_memory_consistency_note_for_injection", return_value=("", {})),
            patch("runtimes.memory.workflow_service.workflow_memory_service.build_hints_block", return_value=""),
        ):
            supervisor_context = store.build_session_context(
                user_query="帮我总结一下",
                target_role="supervisor",
            )
            subagent_context = store.build_session_context(
                user_query="帮我总结一下",
                target_role="agent:creative_media",
            )

        self.assertIn("[MEMORY SUMMARY]", supervisor_context)
        self.assertIn("[MEMORY MAP]", supervisor_context)
        self.assertIn("[RECENT ACTIVITY TEASER]", supervisor_context)
        self.assertNotIn("[MEMORY SUMMARY]", subagent_context)
        self.assertNotIn("[MEMORY MAP]", subagent_context)
        self.assertNotIn("[RECENT ACTIVITY TEASER]", subagent_context)

    def test_periodic_summary_generation_uses_structured_frontmatter_and_yaml_summary_excerpt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "MEMORY_ROOT", memory_root):
                store = MemoryStore()
                week_dir = memory_root / "daily" / "2026" / "04_april" / "week_16"
                _write_day_log(week_dir / "2026-04-14.md", summaries=["完成了桌面链路稳定性修复"])
                _write_day_log(week_dir / "2026-04-15.md", summaries=["补齐了记忆地图与配置入口"])

                store.save_periodic_summary(
                    tier="week",
                    payload={
                        "summary": "本周完成 memory 结构化摘要收口，并稳定了多处 runtime 行为。",
                        "body": "## 本周连续性\n\n- 收口了 memory 注入。\n- 补齐了 summary frontmatter。",
                    },
                    dt=datetime(2026, 4, 19),
                )

                summary_path = week_dir / "summary.md"
                written = summary_path.read_text(encoding="utf-8")
                self.assertIn('type: "periodic_summary"', written)
                self.assertIn('tier: "week"', written)
                self.assertIn('date: "2026-W16"', written)
                self.assertIn('periodStart: "2026-04-13"', written)
                self.assertIn('periodEnd: "2026-04-19"', written)
                self.assertIn('summary: "本周完成 memory 结构化摘要收口，并稳定了多处 runtime 行为。"', written)
                self.assertIn('children:', written)
                self.assertIn('coverage:', written)
                self.assertEqual(
                    store._read_summary_excerpt(summary_path),
                    "本周完成 memory 结构化摘要收口，并稳定了多处 runtime 行为。",
                )

                hierarchical = store.get_hierarchical_summaries(scope_chain=["global"])
                self.assertIn("Summary: 本周完成 memory 结构化摘要收口，并稳定了多处 runtime 行为。", hierarchical)
                self.assertIn("Coverage:", hierarchical)
                self.assertIn("- 2026-04-13: 未产生记录", hierarchical)
                self.assertIn("- 2026-04-14: 有记录", hierarchical)
                self.assertIn("- 2026-04-19: 未产生记录", hierarchical)
                self.assertNotIn("...(5 more)", hierarchical)

    def test_monthly_summary_input_reads_week_summaries_not_daily_fulltext(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "MEMORY_ROOT", memory_root):
                store = MemoryStore()
                week_dir = memory_root / "daily" / "2026" / "04_april" / "week_16"
                day_path = week_dir / "2026-04-14.md"
                _write_day_log(day_path, summaries=["日记摘要"])
                day_path.write_text(day_path.read_text(encoding="utf-8") + "\nRAW_DAILY_BODY_SHOULD_NOT_BE_READ\n", encoding="utf-8")
                store.save_periodic_summary(
                    tier="week",
                    payload={
                        "summary": "周记摘要：只给月记读取这一层。",
                        "body": "RAW_WEEK_BODY_SHOULD_NOT_BE_NEEDED",
                    },
                    dt=datetime(2026, 4, 19),
                )

                monthly_input = store.get_logs_for_period(tier="month", dt=datetime(2026, 4, 30), scope_chain=["global"])

        self.assertIn("周记摘要：只给月记读取这一层。", monthly_input)
        self.assertIn("Ref: memory://week/2026-W16", monthly_input)
        self.assertNotIn("RAW_DAILY_BODY_SHOULD_NOT_BE_READ", monthly_input)

    def test_backfill_periodic_summaries_rewrites_legacy_summary_files_idempotently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            memory_root = Path(temp_dir) / "memory"
            with patch.object(memory_store_module, "MEMORY_ROOT", memory_root):
                store = MemoryStore()
                week_dir = memory_root / "daily" / "2026" / "04_april" / "week_16"
                _write_day_log(week_dir / "2026-04-14.md", summaries=["完成了工作流调试"])
                _write_day_log(week_dir / "2026-04-18.md", summaries=["补齐了记忆摘要"])
                legacy_summary = week_dir / "summary.md"
                legacy_summary.write_text(
                    "# Week Recap\n\n核心连续性信号：完成了 memory 摘要修复，并补齐了下级日志整理。\n",
                    encoding="utf-8",
                )

                result = store.backfill_periodic_summaries()
                self.assertEqual(result["updatedCount"], 1)
                self.assertEqual(result["touchedRefs"], ["memory://week/2026-W16"])

                upgraded = legacy_summary.read_text(encoding="utf-8")
                self.assertIn('type: "periodic_summary"', upgraded)
                self.assertIn('tier: "week"', upgraded)
                self.assertIn('summary: "核心连续性信号：完成了 memory 摘要修复，并补齐了下级日志整理。"', upgraded)

                rerun = store.backfill_periodic_summaries()
                self.assertEqual(rerun["updatedCount"], 0)


if __name__ == "__main__":
    unittest.main()
