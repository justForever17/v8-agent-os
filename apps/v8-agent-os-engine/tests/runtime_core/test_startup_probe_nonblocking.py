from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_mcp_initialization_does_not_block_engine_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    async def exercise() -> None:
        release = asyncio.Event()

        class PendingMcpManager:
            async def initialize(self) -> None:
                await release.wait()

        monkeypatch.setattr(main, "_get_mcp_manager", lambda: PendingMcpManager())
        app = SimpleNamespace(state=SimpleNamespace())

        await asyncio.wait_for(main._safe_initialize_mcp(app), timeout=0.1)

        assert app.state.mcp_init_task.done() is False
        app.state.mcp_init_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await app.state.mcp_init_task

    asyncio.run(exercise())


def test_windows_standalone_launcher_repairs_directory_links_before_start() -> None:
    source = (
        Path(__file__).resolve().parents[4]
        / "scripts"
        / "run-next-with-managed-auth.mjs"
    ).read_text(encoding="utf-8")

    assert 'fs.symlinkSync(target, candidate, "junction")' in source
    assert "repairWindowsDirectorySymlinks" in source


def test_engine_startup_does_not_eagerly_run_plugin_machine_discovery() -> None:
    source = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")

    assert "warm_machine_discovery" not in source
