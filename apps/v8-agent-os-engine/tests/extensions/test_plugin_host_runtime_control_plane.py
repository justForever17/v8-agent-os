from __future__ import annotations

import sys
import unittest
from pathlib import Path


from core.plugin_host.service import PluginHostService
from runtimes.plugin_host.runtime import PluginHostRuntime


class PluginHostRuntimeStreamTests(unittest.TestCase):
    def test_cumulative_stream_snapshots_are_consumed_as_suffixes(self):
        runtime = PluginHostRuntime()
        snapshots: dict[str, str] = {}
        output: list[str] = []

        for raw in ["😊", "😊 在的", "😊 在的，我是 V8 Agent OS"]:
            output.append(runtime._consume_stream_suffix(snapshots, "model_run", raw))

        final_text = "😊 在的，我是 V8 Agent OS"
        output.append(runtime._consume_terminal_text_suffix(snapshots, "model_run", final_text, "".join(output)))

        self.assertEqual("".join(output), final_text)

    def test_terminal_text_only_adds_unseen_suffix(self):
        runtime = PluginHostRuntime()
        snapshots: dict[str, str] = {}
        output = [runtime._consume_stream_suffix(snapshots, "model_run", "你好")]

        output.append(runtime._consume_terminal_text_suffix(snapshots, "model_run", "你好，我是 V8 Agent OS", "".join(output)))

        self.assertEqual("".join(output), "你好，我是 V8 Agent OS")


class PluginHostSnapshotRefreshTests(unittest.TestCase):
    def test_public_refresh_schedules_background_observation_without_sync_probe(self):
        service = PluginHostService()
        service._cached_public_snapshot = {
            "pluginRoot": "test",
            "pluginExtensionsRoot": "test/extensions",
            "runtimeConfig": {},
            "hostSurface": {},
            "controlSurface": {},
            "plugins": [],
            "summary": {},
        }
        service._last_public_refresh_request_monotonic = 0.0
        service._last_live_refresh_monotonic = 0.0
        scheduled: list[bool] = []

        def fail_if_called():
            raise AssertionError("_fast_refresh_public_snapshot must not run synchronously")

        service._fast_refresh_public_snapshot = fail_if_called  # type: ignore[method-assign]
        service._schedule_background_refresh = lambda *, refresh_registry: scheduled.append(refresh_registry)  # type: ignore[method-assign]

        first = service.refresh_public_snapshot()
        second = service.refresh_public_snapshot()

        self.assertEqual(scheduled, [False])
        self.assertEqual(first["startupState"], "refreshing")
        self.assertEqual(second["startupState"], "refreshing")


if __name__ == "__main__":
    unittest.main()

