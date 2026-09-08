import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from runtimes.extensions.mcp.client import MCPManager, _initialization_metadata


def test_initialization_metadata_reads_camel_case_sdk_contract() -> None:
    initialization = SimpleNamespace(
        serverInfo=SimpleNamespace(name="Godot MCP", version="1.4.2"),
        protocolVersion="2025-06-18",
    )

    assert _initialization_metadata(initialization) == {
        "serverInfoName": "Godot MCP",
        "serverInfoVersion": "1.4.2",
        "protocolVersion": "2025-06-18",
    }


def test_initialization_metadata_reads_snake_case_sdk_contract() -> None:
    initialization = SimpleNamespace(
        server_info={"name": "Figma MCP", "version": "2.3.4"},
        protocol_version="2024-11-05",
    )

    assert _initialization_metadata(initialization) == {
        "serverInfoName": "Figma MCP",
        "serverInfoVersion": "2.3.4",
        "protocolVersion": "2024-11-05",
    }


def test_initialization_metadata_does_not_invent_missing_versions() -> None:
    assert _initialization_metadata(SimpleNamespace()) == {
        "serverInfoName": None,
        "serverInfoVersion": None,
        "protocolVersion": None,
    }


def test_inventory_revision_tracks_in_place_tool_contract_changes() -> None:
    manager = MCPManager()
    tool = SimpleNamespace(name="owned", description="Read owned fixture", args_schema={"type": "object"}, metadata={})
    manager._server_tools = {"owned-server": [tool]}
    manager._server_state = {"owned-server": {"status": "connected"}}
    revision = manager._commit_inventory_revision()
    assert manager._commit_inventory_revision() == revision
    for field, value in (("args_schema", {"type": "object", "required": ["value"]}),
                         ("description", "Updated owned fixture contract"),
                         ("metadata", {"annotations": {"readOnlyHint": False}})):
        setattr(tool, field, value)
        updated = manager._commit_inventory_revision()
        assert updated != revision
        revision = updated


def _owned_notification_manager(monkeypatch):
    from mcp.types import ListResourcesResult, ListToolsResult, Tool
    from runtimes.extensions.mcp import client as module

    sessions = []

    class OwnedSession:
        def __init__(self, _read, _write, *, message_handler):
            self.message_handler = message_handler
            self.field_type = "string"
            self.list_calls = 0
            self.closed = self.fail = self.block = self.cancelled = False
            self.started, self.release = asyncio.Event(), asyncio.Event()
            sessions.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def initialize(self):
            return SimpleNamespace()

        async def list_tools(self, **_kwargs):
            self.list_calls += 1
            self.started.set()
            if self.fail:
                raise RuntimeError("owned list failure")
            if self.block:
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            return ListToolsResult(tools=[Tool(name="owned_tool", description="Owned notification fixture",
                inputSchema={"type": "object", "properties": {"value": {"type": self.field_type}}})])

        async def list_resources(self):
            return ListResourcesResult(resources=[])

    @asynccontextmanager
    async def transport(**_kwargs):
        yield None, None

    monkeypatch.setattr(module, "ClientSession", OwnedSession)
    monkeypatch.setattr(module, "sse_client", transport)
    monkeypatch.setattr(module.storage, "get_mcp_config", lambda: {"mcpServers": {"owned": {"type": "sse", "url": "https://owned.invalid/mcp"}}})
    return MCPManager(), sessions


async def _wait_for_owned_state(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_tool_list_notifications_coalesce_update_schema_and_publish_snapshot(monkeypatch):
    async def exercise():
        from mcp.types import ServerNotification, ToolListChangedNotification
        from runtimes.extensions.runtime import ExtensionsRuntimeService

        manager, sessions = _owned_notification_manager(monkeypatch)
        runtime = ExtensionsRuntimeService()
        snapshot = AsyncMock(return_value={})
        monkeypatch.setattr(runtime, "_refresh_runtime_snapshot", snapshot)
        manager.inventory_change_callback = runtime._on_mcp_tools_changed
        try:
            await manager._start_server("owned", {"type": "sse", "url": "https://owned.invalid/mcp"})
            session = sessions[0]
            old_tools = list(manager.get_tools())
            old_revision = manager.get_inventory_revision()
            initial_lists = session.list_calls
            session.field_type = "integer"
            notification = ServerNotification(ToolListChangedNotification())
            for _ in range(8):
                await session.message_handler(notification)
            # The receive-loop callback has not run any list request itself.
            assert session.list_calls == initial_lists
            await _wait_for_owned_state(lambda: snapshot.await_count == 1)
            assert session.list_calls == initial_lists + 2  # Tools + existing Apps discovery.
            assert manager.get_tools()[0] is not old_tools[0]
            assert manager.get_tools()[0].args_schema["properties"]["value"]["type"] == "integer"
            assert manager.get_inventory_revision() != old_revision
            assert runtime._last_mcp_inventory_change["reason"] == "tools_list_changed"
            snapshot.assert_awaited_once_with(clear_route_cache=False)

            ready_revision = manager.get_inventory_revision()
            ready_tool = manager.get_tools()[0]
            session.fail = True
            await session.message_handler(notification)
            await _wait_for_owned_state(lambda: manager._server_state["owned"].get("toolsRefreshStatus") == "error")
            assert manager.get_tools()[0] is ready_tool
            assert manager.get_inventory_revision() == ready_revision
            assert manager._server_state["owned"]["lastToolsRefreshError"] == "RuntimeError"
            assert manager.get_status()["owned"]["toolsRefreshStatus"] == "error"
            assert manager.get_health_summary()["degradedServers"][0]["impact"] == "tool_inventory_stale"
            assert snapshot.await_count == 1
            session.fail = False
            await session.message_handler(notification)
            await _wait_for_owned_state(lambda: snapshot.await_count == 2)
            assert manager._server_state["owned"]["toolsRefreshStatus"] == "ready"
            assert manager._server_state["owned"]["lastToolsRefreshError"] is None
            assert manager.get_health_summary()["degraded"] == 0
        finally:
            await manager.cleanup()
        assert session.closed
        assert not [task for task in asyncio.all_tasks() if task.get_name() == "mcp:owned:tools_refresh"]

    asyncio.run(exercise())


@pytest.mark.parametrize("action", ["remove", "reconnect", "cleanup"])
def test_notification_refresh_is_cancelled_with_its_own_connection(monkeypatch, action):
    async def exercise():
        from mcp.types import ServerNotification, ToolListChangedNotification

        manager, sessions = _owned_notification_manager(monkeypatch)
        callback = AsyncMock()
        manager.inventory_change_callback = callback
        config = {"type": "sse", "url": "https://owned.invalid/mcp"}
        try:
            await manager._start_server("owned", config)
            session = sessions[0]
            session.block = True
            session.started.clear()
            notification = ServerNotification(ToolListChangedNotification())
            await session.message_handler(notification)
            await asyncio.wait_for(session.started.wait(), timeout=3)
            if action == "remove":
                await manager.remove_server("owned")
            elif action == "reconnect":
                await manager._start_server("owned", config)
                assert manager.sessions["owned"] is sessions[1]
            else:
                await manager.cleanup()
            assert session.cancelled and session.closed
            await session.message_handler(notification)  # Late notification cannot resurrect the old worker.
            session.release.set()
            await asyncio.sleep(0)
            assert callback.await_count == 0
            if action != "reconnect":
                assert manager.get_tools() == []
                assert "owned" not in manager.sessions
                assert all(not item["appTools"] and not item["uiResources"] for item in manager.get_app_registry()["servers"])
        finally:
            await manager.cleanup()
        assert not [task for task in asyncio.all_tasks() if task.get_name() == "mcp:owned:tools_refresh"]

    asyncio.run(exercise())


def test_finished_refresh_cannot_publish_after_session_identity_changed(monkeypatch):
    async def exercise():
        manager, sessions = _owned_notification_manager(monkeypatch)

        async def ignore(_message):
            pass

        from runtimes.extensions.mcp import client as module
        session = module.ClientSession(None, None, message_handler=ignore)
        session.block = True
        requested, stop = asyncio.Event(), asyncio.Event()
        requested.set()
        manager.sessions["owned"] = session
        callback = AsyncMock()
        manager.inventory_change_callback = callback
        task = asyncio.create_task(manager._refresh_notified_tools("owned", session, requested, stop))
        await asyncio.wait_for(session.started.wait(), timeout=3)
        replacement = object()
        manager.sessions["owned"] = replacement
        revision = manager.get_inventory_revision()
        session.release.set()
        await asyncio.wait_for(task, timeout=3)
        assert manager.sessions["owned"] is replacement
        assert manager.get_tools() == []
        assert manager.get_inventory_revision() == revision
        assert callback.await_count == 0

    asyncio.run(exercise())


def test_notification_refresh_timeout_keeps_last_completed_inventory(monkeypatch):
    async def exercise():
        from mcp.types import ServerNotification, ToolListChangedNotification
        from runtimes.extensions.mcp import client as module

        manager, sessions = _owned_notification_manager(monkeypatch)
        try:
            await manager._start_server("owned", {"type": "sse", "url": "https://owned.invalid/mcp"})
            monkeypatch.setattr(module, "MCP_SERVER_INIT_TIMEOUT_SECONDS", 0.02)
            session = sessions[0]
            previous_tool = manager.get_tools()[0]
            previous_revision = manager.get_inventory_revision()
            session.block = True
            await session.message_handler(ServerNotification(ToolListChangedNotification()))
            await _wait_for_owned_state(lambda: manager._server_state["owned"].get("toolsRefreshStatus") == "error")
            assert manager._server_state["owned"]["lastToolsRefreshError"] == "TimeoutError"
            assert session.cancelled
            assert manager.get_tools()[0] is previous_tool
            assert manager.get_inventory_revision() == previous_revision
        finally:
            await manager.cleanup()

    asyncio.run(exercise())
