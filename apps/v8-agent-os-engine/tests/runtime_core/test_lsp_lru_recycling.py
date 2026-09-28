"""Tests for LSPManager singleton behavior, LRU eviction, and process pooling."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from runtimes.engineering.lsp import (
    LSPManager,
    LSPProcessInstance,
    LSPStatus,
)


def test_lsp_manager_singleton():
    inst1 = LSPManager.get_instance()
    inst2 = LSPManager.get_instance()
    assert inst1 is inst2


def test_lsp_manager_reuses_alive_instance():
    async def _run():
        manager = LSPManager()
        mock_instance = MagicMock(spec=LSPProcessInstance)
        mock_instance.is_alive.return_value = True

        key = manager._normalize_key("ts", "e:/workspace")
        manager._instances[key] = mock_instance
        manager._last_used[key] = 100.0

        with patch("time.time", return_value=250.0):
            result = await manager.get_or_spawn("ts", "e:/workspace")
            assert result is mock_instance
            assert manager._last_used[key] == 250.0

    asyncio.run(_run())


def test_lsp_manager_dead_process_purged_and_respawned():
    async def _run():
        manager = LSPManager()
        dead_instance = MagicMock(spec=LSPProcessInstance)
        dead_instance.is_alive.return_value = False

        fresh_instance = MagicMock(spec=LSPProcessInstance)
        fresh_instance.is_alive.return_value = True
        fresh_instance.handshake = AsyncMock(return_value=True)

        key = manager._normalize_key("ts", "e:/workspace")
        manager._instances[key] = dead_instance
        manager._last_used[key] = 100.0

        with patch.object(
            manager.probe_runner,
            "resolve_launch_command",
            return_value=(["mock-ts"], "local_workspace", "e:/workspace/mock-ts"),
        ), patch(
            "runtimes.engineering.lsp.lsp_process.LSPProcessInstance.spawn",
            AsyncMock(return_value=fresh_instance),
        ):
            result = await manager.get_or_spawn("ts", "e:/workspace")
            assert result is fresh_instance
            assert manager._instances[key] is fresh_instance

    asyncio.run(_run())


def test_lsp_manager_lru_multi_instance_eviction():
    async def _run():
        manager = LSPManager()

        inst_ts = MagicMock(spec=LSPProcessInstance)
        inst_ts.shutdown = AsyncMock()
        inst_py = MagicMock(spec=LSPProcessInstance)
        inst_py.shutdown = AsyncMock()
        inst_rs = MagicMock(spec=LSPProcessInstance)
        inst_rs.shutdown = AsyncMock()

        key_ts = ("ts", "e:/ws_ts")
        key_py = ("py", "e:/ws_py")
        key_rs = ("rust", "e:/ws_rs")

        manager._instances[key_ts] = inst_ts
        manager._last_used[key_ts] = 100.0  # oldest

        manager._instances[key_py] = inst_py
        manager._last_used[key_py] = 200.0  # intermediate

        manager._instances[key_rs] = inst_rs
        manager._last_used[key_rs] = 450.0  # newest

        with patch("time.time", return_value=500.0):
            # Threshold = 300s.
            # ts: 500 - 100 = 400s (> 300s -> evict)
            # py: 500 - 200 = 300s (not > 300s -> keep)
            # rs: 500 - 450 = 50s (not > 300s -> keep)
            evicted = await manager.cleanup_idle_instances(idle_timeout_sec=300.0)

            assert evicted == 1
            assert key_ts not in manager._instances
            assert key_py in manager._instances
            assert key_rs in manager._instances
            inst_ts.shutdown.assert_awaited_once()
            inst_py.shutdown.assert_not_called()
            inst_rs.shutdown.assert_not_called()

    asyncio.run(_run())


def test_lsp_manager_shutdown_all():
    async def _run():
        manager = LSPManager()
        inst1 = MagicMock(spec=LSPProcessInstance)
        inst1.shutdown = AsyncMock()
        inst2 = MagicMock(spec=LSPProcessInstance)
        inst2.shutdown = AsyncMock()

        manager._instances[("ts", "e:/ws1")] = inst1
        manager._instances[("py", "e:/ws2")] = inst2

        await manager.shutdown_all()

        inst1.shutdown.assert_awaited_once()
        inst2.shutdown.assert_awaited_once()
        assert len(manager._instances) == 0
        assert len(manager._last_used) == 0

    asyncio.run(_run())
