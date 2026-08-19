from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from runtimes.extensions.runtime import ExtensionsRuntimeService


class ExtensionsRuntimeStartupAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_cold_start_publishes_refreshing_snapshot_without_waiting_for_catalog(self) -> None:
        service = ExtensionsRuntimeService()

        def slow_catalog() -> dict[str, object]:
            time.sleep(0.12)
            return {"summary": {}, "skills": {}, "mcp": {"servers": []}}

        def build_health(_catalog: dict[str, object]) -> dict[str, object]:
            return {"summary": {}, "skills": {}, "mcp": {}}

        def slow_persist() -> None:
            time.sleep(0.12)

        with (
            patch.object(service, "_load_cache", return_value=False),
            patch.object(service, "_build_catalog_live", side_effect=slow_catalog),
            patch.object(service, "_build_health_live", side_effect=build_health),
            patch.object(service, "_persist_cache", side_effect=slow_persist),
            patch.object(service, "_ensure_skill_inventory_watcher"),
            patch.object(service, "_ensure_mcp_inventory_watcher"),
        ):
            await asyncio.wait_for(service.start(), timeout=0.08)

            startup = service.get_startup_status()
            self.assertEqual(startup.get("startupState"), "refreshing")
            self.assertEqual(startup.get("snapshotFreshness"), "cold")
            self.assertEqual(service.build_catalog().get("catalogSummary", {}).get("skillCount"), 0)

            refresh_task = service._background_refresh_task
            self.assertIsNotNone(refresh_task)
            assert refresh_task is not None
            await asyncio.sleep(0.03)
            self.assertFalse(refresh_task.done())
            await refresh_task

        self.assertEqual(service.get_startup_status().get("startupState"), "ready")
        self.assertEqual(service.get_startup_status().get("snapshotFreshness"), "live")

    async def test_concurrent_start_uses_one_background_refresh_without_sync_catalog_build(self) -> None:
        service = ExtensionsRuntimeService()
        build_calls = 0

        def cold_catalog() -> dict[str, object]:
            nonlocal build_calls
            build_calls += 1
            time.sleep(0.03)
            return {"summary": {}, "skills": {}, "mcp": {"servers": []}}

        def build_health(_catalog: dict[str, object]) -> dict[str, object]:
            return {"summary": {}, "skills": {}, "mcp": {}}

        with (
            patch.object(service, "_load_cache", return_value=False),
            patch.object(service, "_build_catalog_live", side_effect=cold_catalog),
            patch.object(service, "_build_health_live", side_effect=build_health),
            patch.object(service, "_persist_cache"),
            patch.object(service, "_persist_cache_payload"),
            patch.object(service, "_ensure_skill_inventory_watcher"),
            patch.object(service, "_ensure_mcp_inventory_watcher"),
        ):
            await asyncio.gather(service.start(), service.start())
            refresh_task = service._background_refresh_task
            self.assertIsNotNone(refresh_task)
            assert refresh_task is not None
            await refresh_task

        self.assertEqual(build_calls, 1)
        self.assertEqual(service.get_startup_status().get("startupState"), "ready")

    async def test_background_snapshot_waits_for_skill_refresh_before_catalog_build(self) -> None:
        service = ExtensionsRuntimeService()
        release_skill_refresh = asyncio.Event()
        skill_refresh_done = False
        catalog_observed_skill_refresh = False

        async def refresh_skills() -> None:
            nonlocal skill_refresh_done
            await release_skill_refresh.wait()
            skill_refresh_done = True

        def build_catalog() -> dict[str, object]:
            nonlocal catalog_observed_skill_refresh
            catalog_observed_skill_refresh = skill_refresh_done
            return {"summary": {}, "skills": {}, "mcp": {"servers": []}}

        skill_task = asyncio.create_task(refresh_skills())
        with (
            patch.object(service, "_load_cache", return_value=False),
            patch.object(service, "_build_catalog_live", side_effect=build_catalog),
            patch.object(service, "_build_health_live", return_value={"summary": {}, "skills": {}, "mcp": {}}),
            patch.object(service, "_persist_cache_payload"),
            patch.object(service, "_ensure_skill_inventory_watcher"),
            patch.object(service, "_ensure_mcp_inventory_watcher"),
        ):
            await service.start(skill_refresh_task=skill_task, wait_for_initial_refresh=False)
            await asyncio.sleep(0.02)
            self.assertFalse(catalog_observed_skill_refresh)
            release_skill_refresh.set()
            assert service._background_refresh_task is not None
            await service._background_refresh_task

        self.assertTrue(catalog_observed_skill_refresh)

    async def test_start_can_publish_cached_state_without_waiting_for_optional_refresh(self) -> None:
        service = ExtensionsRuntimeService()
        service._cached_catalog = {"summary": {}, "skills": {}, "mcp": {"servers": []}}
        service._cached_health = {"summary": {}, "skills": {}, "mcp": {}}
        pending = asyncio.create_task(asyncio.sleep(0.2))
        with (
            patch.object(service, "_ensure_skill_inventory_watcher"),
            patch.object(service, "_ensure_mcp_inventory_watcher"),
        ):
            await asyncio.wait_for(
                service.start(
                    skill_refresh_task=pending,
                    mcp_init_task=pending,
                    wait_for_initial_refresh=False,
                ),
                timeout=0.08,
            )
        refresh_task = service._background_refresh_task
        self.assertIsNotNone(refresh_task)
        assert refresh_task is not None
        await refresh_task
        await pending

    async def test_inventory_refresh_aggregates_to_one_snapshot_build(self) -> None:
        service = ExtensionsRuntimeService()
        skill_change = {"changed": True, "revision": "skills:2"}
        mcp_change = {"changed": True, "inventoryRevision": "mcp:2"}
        refresh = AsyncMock()
        with (
            patch.object(service, "_refresh_skill_inventory_if_changed", new=AsyncMock(return_value=skill_change)),
            patch.object(service, "_refresh_mcp_inventory_if_changed", new=AsyncMock(return_value=mcp_change)),
            patch.object(service, "_refresh_runtime_snapshot", new=refresh),
        ):
            result = await service.refresh_inventory_if_changed(reason="test")

        self.assertTrue(result.get("changed"))
        refresh.assert_awaited_once_with(clear_route_cache=False)


if __name__ == "__main__":
    unittest.main()
